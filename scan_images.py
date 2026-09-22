import os
import re
import shutil
import logging
import argparse
from datetime import datetime
from typing import List

import numpy as np
import pytesseract
from PIL import Image, ImageOps, ImageFilter
from pyzbar.pyzbar import decode
from isbnlib import meta, notisbn, is_isbn10, is_isbn13
import pandas as pd
from pandas import DataFrame

from sort_images import create_subfolders_for_consecutive_pairs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

# Fixed column order for the catalogue. These are the keys isbnlib.meta() returns.
# Using a fixed list means the spreadsheet always has headers, even on a run where
# nothing scans, and the columns never reorder between runs.
COLUMNS = ["Title", "Authors", "Year", "Publisher", "Language", "ISBN-13"]

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
    Yield preprocessed versions of an image for OCR, cheapest/most likely first.
    The caller stops as soon as one variant produces a valid ISBN, so the second
    (binarised) pass only costs time on images where the first one failed.
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


def _normalise_run(run: str) -> str:
    '''Apply OCR digit fixes to a numeric-looking run and strip separators.'''
    fixed = run.translate(OCR_DIGIT_FIXES)
    return re.sub(r"[^0-9X]", "", fixed)


def extract_isbn_candidates(text: str) -> List[str]:
    '''
    Pull checksum-valid ISBN candidates out of raw OCR text, ISBN-13s first.

    ISBN-13: any 13-digit window starting 978/979 inside a numeric run. Sliding
    windows handle runs where OCR glued extra digits on, e.g. the barcode's
    human-readable line followed by a price add-on ("9780140449136 51599").

    ISBN-10: only accepted when it directly follows an "ISBN" label. Without the
    978/979 prefix, roughly 1 in 11 random 10-digit strings passes the checksum,
    and isbnlib would happily return metadata for the wrong book.
    '''
    candidates = []

    for match in DIGIT_RUN.finditer(text):
        run = match.group()
        if sum(c.isdigit() for c in run) < MIN_REAL_DIGITS:
            continue
        digits = _normalise_run(run).replace("X", "")
        for i in range(len(digits) - 12):
            window = digits[i:i + 13]
            if window[:3] in ("978", "979") and is_isbn13(window):
                candidates.append(window)

    for label in ISBN_LABEL.finditer(text):
        match = DIGIT_RUN.match(text, label.end())
        if not match or sum(c.isdigit() for c in match.group()) < MIN_REAL_DIGITS:
            continue
        digits = _normalise_run(match.group())
        window = digits[:10]
        if len(window) == 10 and is_isbn10(window):
            candidates.append(window)

    # De-duplicate while keeping order (ISBN-13s stay ahead of ISBN-10s).
    return list(dict.fromkeys(candidates))


# --- Extraction strategies --------------------------------------------------

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
                return isbn_data

    return {}


def data_from_isbn_ocr(images: List[Image.Image]) -> dict:
    '''
    OCR each image (orientation already corrected at load time) through a
    sequence of preprocessing variants, extract ISBN-13/ISBN-10 candidates,
    and look each up via isbnlib, same as the barcode path. Returns the first
    successful lookup, or an empty dictionary if nothing is found.
    '''
    tried = set()

    for image in images:
        for variant_name, processed in ocr_variants(image):
            try:
                text = pytesseract.image_to_string(processed, config=TESSERACT_CONFIG)
            except Exception as e:
                logger.warning("Failed to OCR image (%s) for ISBN text: %s", variant_name, e)
                continue

            for candidate in extract_isbn_candidates(text):
                if candidate in tried:
                    continue
                tried.add(candidate)

                try:
                    isbn_data = meta(candidate)
                except Exception as e:
                    logger.warning("Failed to fetch metadata for OCR'd ISBN %s: %s", candidate, e)
                    continue

                if isbn_data:
                    logger.info("Recovered ISBN %s via OCR fallback (%s pass)", candidate, variant_name)
                    return isbn_data

    return {}


def data_from_cover_ocr(images: List[Image.Image]) -> dict:
    '''
    Fallback plan for when no ISBN can be found by barcode or OCR-of-ISBN-text.
    Runs OCR across the whole cover and attempts to infer title/author from
    the raw text (e.g. largest text block heuristics, or a lookup against a
    books API by extracted title string).
    '''
    # TODO: implement. This is the least reliable path and should be a last resort.
    return {}


def get_book_data(subfolder: str) -> dict:
    '''
    Load all images in a subfolder (assumed to be one book's pair of photos)
    and try each extraction strategy in order of reliability: barcode,
    then OCR'd ISBN text, then full cover OCR.
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
    if not data:
        data = data_from_isbn_ocr(images)
    if not data:
        data = data_from_cover_ocr(images)

    return data


# --- Output -----------------------------------------------------------------

def write_catalogue(df: DataFrame, output_path: str) -> str:
    '''
    Write the catalogue to .xlsx with a frozen header row, an autofilter on
    every column (so it can be sorted/filtered by Author, Year, etc. in one
    click), and column widths sized to the content.

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


def scan_all_folders(subfolders: list, not_scanned_folder: str, output_path: str):
    '''
    Scan every subfolder for book metadata. Successfully identified books
    are collected into a single DataFrame and written out to an Excel
    spreadsheet (matching the README's promise of a digital library
    catalogue, listable by Author or Title). Subfolders where no data
    could be extracted are moved into not_scanned_folder for manual
    review.
    '''
    rows = []

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
            logger.info("Scanned %s: %s", subfolder, row.get("Title", "unknown title"))
        else:
            logger.warning("Could not identify book in %s, moving to %s", subfolder, not_scanned_folder)
            dest = os.path.join(not_scanned_folder, os.path.basename(subfolder))
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
    logger.info("Wrote %d books to %s", len(rows), written)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-f", "--folder", type=str, required=True,
        help="folder containing sorted Pair_N subfolders (output of sort_images.py)",
    )
    parser.add_argument(
        "-n", "--not-scanned-folder", type=str, required=True,
        help="destination folder for pairs that could not be identified",
    )
    parser.add_argument(
        "-o", "--output", type=str, required=True,
        help="output .xlsx catalogue file",
    )
    args = parser.parse_args()

    sorted_folders = args.folder
    not_scanned_folder = args.not_scanned_folder
    output_path = args.output

    if not output_path.lower().endswith(".xlsx"):
        logger.warning("Output path %s does not end in .xlsx; appending extension", output_path)
        output_path += ".xlsx"

    subfolders = [
        os.path.join(sorted_folders, subfolder)
        for subfolder in os.listdir(sorted_folders)
        if os.path.isdir(os.path.join(sorted_folders, subfolder))
    ]

    os.makedirs(not_scanned_folder, exist_ok=True)

    scan_all_folders(subfolders, not_scanned_folder, output_path)
