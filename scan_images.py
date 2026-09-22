import os
import re
import shutil
import logging
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageFilter
from pyzbar.pyzbar import decode
from isbnlib import meta, notisbn, is_isbn10, is_isbn13
import pandas as pd
from pandas import DataFrame

from sort_images import IMAGE_EXTENSIONS
from pillow_heif import register_heif_opener

register_heif_opener()  # lets Image.open() read .heic/.heif

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".heif")

logger = logging.getLogger(__name__)

# --- Provenance -------------------------------------------------------------

SOURCE_BARCODE = "Barcode"
SOURCE_ISBN_OCR = "ISBN OCR"
SOURCE_COVER_OCR = "Cover OCR"

# Confidence levels:
#   High   - barcode decoded. EAN-13 has its own check digit, so a misread is very rare.
#   Medium - ISBN read by OCR on the first (greyscale) pass with no digit corrections.
#   Low    - ISBN read by OCR only after binarising, or only after swapping look-alike
#            letters for digits (O->0, l->1, ...). Checksum-valid, but worth a glance.
# Cover OCR, once implemented, should map its fuzzy-match score onto these same labels.
HIGH, MEDIUM, LOW = "High", "Medium", "Low"

# Fixed column order for the catalogue. The first six are the keys isbnlib.meta()
# returns; Source and Confidence are added by this script. Using a fixed list means
# the spreadsheet always has headers, even on a run where nothing scans, and the
# columns never reorder between runs.
COLUMNS = ["Title", "Authors", "Year", "Publisher", "Language", "ISBN-13", "Source", "Confidence"]

# --- ISBN-OCR configuration -------------------------------------------------

# Characters Tesseract commonly produces in place of digits. Applied ONLY inside
# runs that already look numeric (see DIGIT_RUN), never to the whole OCR text,
# otherwise the word "ISBN" itself would be turned into "158N".
OCR_DIGIT_FIXES = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2", "z": "2",
    "G": "6",
    "x": "X",
})
_DIGITISH = r"0-9OoQDIil|SsBZzG"

# A run of digit-like characters, optionally separated by hyphens/spaces, that may
# end in X (ISBN-10 check digit). Long enough to hold at least an ISBN-10.
DIGIT_RUN = re.compile(rf"[{_DIGITISH}][{_DIGITISH}\- ]{{8,24}}[{_DIGITISH}Xx]")

# Minimum number of genuine digits a run must contain before OCR fixes are applied.
# Stops ordinary words made entirely of look-alike letters ("SOLD IS BIG") from
# being translated into numbers.
MIN_REAL_DIGITS = 8

# The "ISBN" label, tolerant of the usual OCR confusions (1SBN, I5BN, ISB N),
# optionally followed by -10 / -13 and a colon.
ISBN_LABEL = re.compile(r"[I1l|]\s?[S5]\s?[B8]\s?N(?:[\s-]?1[03])?\s*[:.]?\s*", re.IGNORECASE)

# --psm 11 = sparse text: find as much text as possible in no particular order.
# Suits a back cover where the ISBN is a small isolated line, not a paragraph.
TESSERACT_CONFIG = "--psm 11"

# Photos with a longer side below this are upscaled before OCR. Small ISBN text
# on low-resolution images is where Tesseract loses the most accuracy. Full-size
# phone photos (~4000 px) are left alone: upscaling them costs time for no gain.
MIN_OCR_LONG_SIDE = 2500

# Preprocessing variants, in the order they are tried. Must match ocr_variants().
VARIANT_NAMES = ("greyscale", "binarised")


def load_image(path: str) -> Image.Image:
    '''
    Open an image, apply its EXIF orientation, and return a fully loaded copy
    so the underlying file handle is closed.

    Phones usually save photos in sensor orientation and store the real rotation
    in an EXIF tag. Viewers apply it; Image.open() does not. Without this step a
    portrait photo arrives sideways and OCR on it largely fails.
    '''
    with Image.open(path) as im:
        oriented = ImageOps.exif_transpose(im)
        oriented.load()
        return oriented.copy()


def _otsu_threshold(gray: Image.Image) -> int:
    '''Return the Otsu threshold (0-255) for a greyscale image.'''
    hist = np.bincount(np.asarray(gray).ravel(), minlength=256).astype(float)
    total = hist.sum()
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    with np.errstate(divide="ignore", invalid="ignore"):
        between_var = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    if np.all(np.isnan(between_var)):
        # Uniform image (e.g. a blank frame): no meaningful threshold exists.
        return 128
    return int(np.nanargmax(between_var))


def ocr_variants(image: Image.Image):
    '''
    Yield (name, image) preprocessed versions of an image for OCR, in the
    order given by VARIANT_NAMES. Preprocessing is cheap; the expensive part
    is the Tesseract call, which OcrCache only makes when a variant is asked for.
    '''
    gray = ImageOps.grayscale(image)

    long_side = max(gray.size)
    if long_side < MIN_OCR_LONG_SIDE:
        scale = MIN_OCR_LONG_SIDE / long_side
        gray = gray.resize((round(gray.width * scale), round(gray.height * scale)), Image.LANCZOS)

    # cutoff=0 (stretch min..max only). A percentage cutoff looks harmless but on a
    # back cover the ink is often under 1% of pixels, so clipping 1% at the dark
    # end lands inside the white background and blows JPEG noise up into garbage.
    gray = ImageOps.autocontrast(gray, cutoff=0).filter(ImageFilter.SHARPEN)
    yield "greyscale", gray

    threshold = _otsu_threshold(gray)
    yield "binarised", gray.point(lambda p: 255 if p > threshold else 0)


# --- OCR cache --------------------------------------------------------------

def data_to_text(data: dict) -> str:
    '''
    Rebuild plain text from pytesseract.image_to_data() output: words joined
    by spaces within a line, lines joined by newlines. Equivalent to what
    image_to_string() returns, so the ISBN regexes behave the same.
    '''
    lines: Dict[tuple, List[str]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["page_num"][i], data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    return "\n".join(" ".join(words) for words in lines.values())


class OcrCache:
    '''
    OCR results for one book's images, computed at most once per
    (image, variant) no matter how many strategies ask for them.

    Stores image_to_data() output rather than plain text: words plus bounding
    boxes and per-word confidence. The ISBN path reduces it to text; the cover
    path (once implemented) needs the boxes to estimate font size and pick out
    the title, so both can share one Tesseract call.
    '''

    def __init__(self, images: List[Image.Image]):
        self.images = images
        self._variants: Dict[int, Dict[str, Image.Image]] = {}
        self._data: Dict[Tuple[int, str], dict] = {}

    def variant(self, index: int, name: str) -> Image.Image:
        if index not in self._variants:
            self._variants[index] = dict(ocr_variants(self.images[index]))
        return self._variants[index][name]

    def data(self, index: int, name: str) -> dict:
        key = (index, name)
        if key not in self._data:
            self._data[key] = pytesseract.image_to_data(
                self.variant(index, name),
                config=TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT,
            )
        return self._data[key]

    def text(self, index: int, name: str) -> str:
        return data_to_text(self.data(index, name))


# --- ISBN extraction from OCR text ------------------------------------------

def _normalise_run(run: str) -> str:
    '''Apply OCR digit fixes to a numeric-looking run and strip separators.'''
    fixed = run.translate(OCR_DIGIT_FIXES)
    return re.sub(r"[^0-9X]", "", fixed)


def _literal_digits(run: str) -> str:
    '''The run's digits exactly as OCR read them, with no look-alike fixes.'''
    return re.sub(r"[^0-9X]", "", run.replace("x", "X"))


def extract_isbn_candidates(text: str) -> List[Tuple[str, bool]]:
    '''
    Pull checksum-valid ISBN candidates out of raw OCR text, ISBN-13s first.
    Returns (isbn, corrected) pairs, where corrected is True if the ISBN only
    exists after swapping look-alike letters for digits.

    ISBN-13: any 13-digit window starting 978/979 inside a numeric run. Sliding
    windows handle runs where OCR glued extra digits on, e.g. the barcode's
    human-readable line followed by a price add-on ("9780140449136 51599").

    ISBN-10: only accepted when it directly follows an "ISBN" label. Without the
    978/979 prefix, roughly 1 in 11 random 10-digit strings passes the checksum,
    and isbnlib would happily return metadata for the wrong book.
    '''
    candidates: List[Tuple[str, bool]] = []

    for match in DIGIT_RUN.finditer(text):
        run = match.group()
        if sum(c.isdigit() for c in run) < MIN_REAL_DIGITS:
            continue
        digits = _normalise_run(run).replace("X", "")
        literal = _literal_digits(run).replace("X", "")
        for i in range(len(digits) - 12):
            window = digits[i:i + 13]
            if window[:3] in ("978", "979") and is_isbn13(window):
                candidates.append((window, window not in literal))

    for label in ISBN_LABEL.finditer(text):
        match = DIGIT_RUN.match(text, label.end())
        if not match or sum(c.isdigit() for c in match.group()) < MIN_REAL_DIGITS:
            continue
        digits = _normalise_run(match.group())
        window = digits[:10]
        if len(window) == 10 and is_isbn10(window):
            candidates.append((window, window not in _literal_digits(match.group())))

    # De-duplicate while keeping order (ISBN-13s stay ahead of ISBN-10s). If the
    # same ISBN was seen both with and without corrections, keep the clean reading.
    best: Dict[str, bool] = {}
    for isbn, corrected in candidates:
        if isbn not in best or (best[isbn] and not corrected):
            best[isbn] = corrected
    return list(best.items())


# --- Extraction strategies --------------------------------------------------
# Each strategy returns isbnlib metadata plus "Source" and "Confidence", or {}.

def data_from_barcode(images: List[Image.Image]) -> dict:
    '''
    Iterate through a list of pillow images scanning for barcodes.
    If a barcode is found decode it and check if it
    matches an isbn code. If yes return the isbn data,
    if not keep iterating and eventually return an empty dictionary.
    '''
    for image in images:
        try:
            decoded_objects = decode(image)
        except Exception as e:
            logger.warning("Failed to decode barcode from image: %s", e)
            continue

        for decoded in decoded_objects:
            isbn_code = decoded.data.decode("utf-8")

            if notisbn(isbn_code):
                continue

            try:
                isbn_data = meta(isbn_code)
            except Exception as e:
                logger.warning("Failed to fetch metadata for %s: %s", isbn_code, e)
                continue

            if isbn_data:
                return {**isbn_data, "Source": SOURCE_BARCODE, "Confidence": HIGH}

    return {}


def data_from_isbn_ocr(cache: OcrCache) -> dict:
    '''
    OCR each image through the preprocessing variants, extract ISBN-13/ISBN-10
    candidates, and look each up via isbnlib, same as the barcode path.
    Returns the first successful lookup, or an empty dictionary.

    Order is variant-first: the greyscale pass runs on every image before any
    image is binarised. The front cover rarely carries an ISBN, so this avoids
    a wasted binarised OCR of the cover when the back's greyscale pass succeeds.
    '''
    tried = set()

    for variant_name in VARIANT_NAMES:
        for index in range(len(cache.images)):
            try:
                text = cache.text(index, variant_name)
            except Exception as e:
                logger.warning("Failed to OCR image (%s) for ISBN text: %s", variant_name, e)
                continue

            for candidate, corrected in extract_isbn_candidates(text):
                if candidate in tried:
                    continue
                tried.add(candidate)

                try:
                    isbn_data = meta(candidate)
                except Exception as e:
                    logger.warning("Failed to fetch metadata for OCR'd ISBN %s: %s", candidate, e)
                    continue

                if isbn_data:
                    confidence = MEDIUM if variant_name == "greyscale" and not corrected else LOW
                    logger.info(
                        "Recovered ISBN %s via OCR fallback (%s pass%s)",
                        candidate, variant_name, ", digits corrected" if corrected else "",
                    )
                    return {**isbn_data, "Source": SOURCE_ISBN_OCR, "Confidence": confidence}

    return {}


def data_from_cover_ocr(cache: OcrCache) -> dict:
    '''
    Fallback plan for when no ISBN can be found by barcode or OCR-of-ISBN-text.
    Infers title/author from the cover's text and looks them up in a books API.

    By the time this runs, data_from_isbn_ocr has already OCR'd every image, so
    cache.data(index, "greyscale") returns Tesseract's words, bounding boxes
    (left/top/width/height) and confidences without another OCR call. Box
    height is the font-size proxy for picking out the title.

    Should return Source=SOURCE_COVER_OCR and a Confidence derived from the
    fuzzy-match score.
    '''
    # TODO: implement. This is the least reliable path and should be a last resort.
    return {}


def get_book_data(subfolder: str) -> dict:
    '''
    Load all images in a subfolder (assumed to be one book's pair of photos)
    and try each extraction strategy in order of reliability: barcode,
    then OCR'd ISBN text, then full cover OCR. The two OCR strategies share
    one OcrCache so no image is OCR'd twice.
    '''
    image_paths = sorted(
        os.path.join(subfolder, f)
        for f in os.listdir(subfolder)
        if f.lower().endswith(IMAGE_EXTENSIONS)
    )

    images = []
    for path in image_paths:
        try:
            images.append(load_image(path))
        except Exception as e:
            logger.warning("Could not open image %s: %s", path, e)

    data = data_from_barcode(images)
    if data:
        return data

    cache = OcrCache(images)
    data = data_from_isbn_ocr(cache)
    if not data:
        data = data_from_cover_ocr(cache)

    return data


# --- Output -----------------------------------------------------------------

def write_catalogue(df: DataFrame, output_path: str) -> str:
    '''
    Write the catalogue to .xlsx with a frozen header row, an autofilter on
    every column (so it can be sorted/filtered by Author, Year, etc. in one
    click), column widths sized to the content, and Low-confidence cells
    highlighted so they stand out for a manual check.

    If the target file can't be written (typically because it is open in
    Excel on Windows, which locks it), the catalogue is saved to a
    timestamped file next to it instead so the run's results aren't lost.
    Returns the path actually written.
    '''
    def _write(path: str):
        with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Catalogue")
            sheet = writer.sheets["Catalogue"]

            sheet.freeze_panes(1, 0)
            # Autofilter over the header plus all data rows (header only if empty).
            sheet.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)

            for col_idx, col in enumerate(df.columns):
                longest = max([len(str(col))] + [len(str(v)) for v in df[col].dropna()])
                sheet.set_column(col_idx, col_idx, min(longest + 2, 60))

            if len(df) and "Confidence" in df.columns:
                col_idx = df.columns.get_loc("Confidence")
                low_fmt = writer.book.add_format({"bg_color": "#FFEB9C", "font_color": "#9C5700"})
                sheet.conditional_format(1, col_idx, len(df), col_idx, {
                    "type": "cell", "criteria": "==", "value": f'"{LOW}"', "format": low_fmt,
                })

    try:
        _write(output_path)
        return output_path
    except PermissionError:
        base, ext = os.path.splitext(output_path)
        fallback = f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
        logger.error(
            "Could not write %s (is it open in Excel?). Saving to %s instead.",
            output_path, fallback,
        )
        _write(fallback)
        return fallback


def list_pair_folders(folder: str) -> List[str]:
    '''Return the immediate subfolders of folder (the Pair_N folders).'''
    return [
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if os.path.isdir(os.path.join(folder, name))
    ]


def _unique_destination(folder: str, name: str) -> str:
    '''
    Return folder/name, or folder/name_2, name_3, ... if that already exists.
    shutil.move() into an existing directory nests the source inside it rather
    than failing, so a second run would otherwise bury Pair_3 inside the old Pair_3.
    '''
    dest = os.path.join(folder, name)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(folder, f"{name}_{n}")
        n += 1
    return dest


def scan_all_folders(subfolders: list, not_scanned_folder: str, output_path: str) -> Tuple[str, int, int]:
    '''
    Scan every subfolder for book metadata. Successfully identified books
    are collected into a single DataFrame and written out to an Excel
    spreadsheet (matching the README's promise of a digital library
    catalogue, listable by Author or Title). Subfolders where no data
    could be extracted are moved into not_scanned_folder for manual
    review.

    Returns (path written, number identified, number not identified).
    '''
    os.makedirs(not_scanned_folder, exist_ok=True)
    rows = []
    unidentified = 0

    for subfolder in sorted(subfolders):
        try:
            book_data = get_book_data(subfolder)
        except Exception as e:
            logger.error("Failed to process %s: %s", subfolder, e)
            book_data = {}

        if book_data:
            # isbnlib.meta() returns 'Authors' as a list; flatten it so it fits
            # cleanly into a single spreadsheet cell.
            row = dict(book_data)
            if isinstance(row.get("Authors"), list):
                row["Authors"] = ", ".join(row["Authors"])
            rows.append(row)
            logger.info(
                "Scanned %s: %s [%s, %s]", subfolder, row.get("Title", "unknown title"),
                row.get("Source"), row.get("Confidence"),
            )
        else:
            unidentified += 1
            dest = _unique_destination(not_scanned_folder, os.path.basename(subfolder))
            logger.warning("Could not identify book in %s, moving to %s", subfolder, dest)
            try:
                shutil.move(subfolder, dest)
            except Exception as e:
                logger.error("Failed to move %s to %s: %s", subfolder, dest, e)

    data_all_books = DataFrame(rows, columns=COLUMNS)
    if not data_all_books.empty:
        data_all_books = data_all_books.sort_values(
            "Title", key=lambda s: s.fillna("").str.lower()
        )

    written = write_catalogue(data_all_books, output_path)
    logger.info("Wrote %d books to %s (%d not identified)", len(rows), written, unidentified)
    return written, len(rows), unidentified
