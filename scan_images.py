import os
import re
import shutil
import logging
import argparse
from typing import List

import pytesseract
from PIL import Image
from pyzbar.pyzbar import decode
from isbnlib import meta, notisbn
import pandas as pd
from pandas import DataFrame

from sort_images import create_subfolders_for_consecutive_pairs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Matches ISBN-13 (starts with 978/979), with optional hyphens or spaces between groups.
ISBN13_PATTERN = re.compile(r"97[89][- ]?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?\d")


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
    Iterate through a list of images running OCR and scanning the extracted
    text for something that looks like an ISBN-13 (regex match on the
    978/979 prefix). Each candidate is stripped of hyphens/spaces and
    validated/looked up via isbnlib, same as the barcode path. Returns the
    first successful lookup, or an empty dictionary if nothing is found.
    '''
    for image in images:
        try:
            text = pytesseract.image_to_string(image)
        except Exception as e:
            logger.warning("Failed to OCR image for ISBN text: %s", e)
            continue

        for match in ISBN13_PATTERN.findall(text):
            candidate = re.sub(r"[- ]", "", match)

            if notisbn(candidate):
                continue

            try:
                isbn_data = meta(candidate)
            except Exception as e:
                logger.warning("Failed to fetch metadata for OCR'd ISBN %s: %s", candidate, e)
                continue

            if isbn_data:
                logger.info("Recovered ISBN %s via OCR fallback", candidate)
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
    image_paths = [
        os.path.join(subfolder, f)
        for f in os.listdir(subfolder)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    images = []
    for path in image_paths:
        try:
            images.append(Image.open(path))
        except Exception as e:
            logger.warning("Could not open image %s: %s", path, e)

    data = data_from_barcode(images)
    if not data:
        data = data_from_isbn_ocr(images)
    if not data:
        data = data_from_cover_ocr(images)

    return data


def scan_all_folders(subfolders: list, not_scanned_folder: str, output_path: str):
    '''
    Scan every subfolder for book metadata. Successfully identified books
    are appended to a single DataFrame and written out to an Excel
    spreadsheet (matching the README's promise of a digital library
    catalogue, listable by Author or Title). Subfolders where no data
    could be extracted are copied into not_scanned_folder for manual
    review.
    '''
    rows = []

    for subfolder in subfolders:
        try:
            book_data = get_book_data(subfolder)
        except Exception as e:
            logger.error("Failed to process %s: %s", subfolder, e)
            book_data = {}

        if book_data:
            # isbnlib.meta() returns fields like 'Authors' as a list;
            # flatten that so it fits cleanly into a single CSV cell.
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

    data_all_books = DataFrame(rows)
    if not data_all_books.empty and "Title" in data_all_books.columns:
        data_all_books = data_all_books.sort_values("Title")

    data_all_books.to_excel(output_path, index=False, engine="xlsxwriter")
    logger.info("Wrote %d books to %s", len(rows), output_path)


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
