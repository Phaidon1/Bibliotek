# Bibliotek

Project Bibliotek:

Sorts images of the front and back of books into pairs, which are placed into folders.

Each pair is then scanned for a barcode. If no barcode can be read, the script falls back to reading the printed ISBN number with OCR.

Once an ISBN is found, the script looks up the Title, Author, Publisher and publishing year of the book.

This information is written to an Excel spreadsheet containing every book that has been photographed and identified. The sheet has a filter on every column, so it can be sorted by Author, Title, Year, etc. Each row also records how the book was identified and how much to trust it.

In this way a digital library and archiving system is created.

## Requirements

Python packages:
```
pip install -r requirements.txt
```

System libraries:

- Zbar - https://pypi.org/project/pyzbar/
- Tesseract - https://github.com/tesseract-ocr/tesseract

### Zbar

Install [Zbar](https://pypi.org/project/pyzbar/) to be able to read barcodes

macOS
```
brew install zbar
```
Linux
```
sudo apt-get install libzbar0
```

### Tesseract

Install [Tesseract](https://github.com/tesseract-ocr/tesseract) in order to scan the images for text

macOS
```
brew install tesseract
brew install tesseract-lang   # optional: additional languages
```
Linux
```
sudo apt install tesseract-ocr-all
```

## Usage

Photos must be taken two per book (cover, then back/barcode), so that sorting the files by name puts each book's photos next to each other.

Everything runs through `bibliotek.py`, which has three subcommands:

| Command | What it does |
|---|---|
| `run`  | Sort and scan in one step. The usual choice. |
| `sort` | Only pair the photos into `Pair_N` folders. |
| `scan` | Only scan folders that are already sorted, e.g. to re-scan after changing the OCR settings. |

### run

```
python bibliotek.py run -f FOLDER [-n NOT_SCANNED_FOLDER] [-o OUTPUT] [--keep-pairs DIR]
```

Example:
```
python bibliotek.py run -f ~/Pictures/books
```

This writes, next to the photo folder:

- `books_catalogue.xlsx` – the catalogue.
- `books_not_scanned/` – pairs that could not be identified, for manual review.

`-n` and `-o` override those locations. The `Pair_N` folders are made in a temporary folder and deleted afterwards (they are only copies of your photos; unidentified pairs are moved out first). Use `--keep-pairs DIR` to keep them, which must be a new or empty folder.

### sort

```
python bibliotek.py sort -f FOLDER [-o OUTPUT]
```

Copies the photos into `Pair_1`, `Pair_2`, … inside `OUTPUT` (default `FOLDER_sorted`). The original photos are not touched.

### scan

```
python bibliotek.py scan -f FOLDER [-n NOT_SCANNED_FOLDER] [-o OUTPUT]
```

Scans the `Pair_N` folders inside `FOLDER`. Defaults are `FOLDER_not_scanned/` and `FOLDER_catalogue.xlsx`. Unidentified pairs are **moved** out of `FOLDER` into the not-scanned folder.

## How books are identified

For each `Pair_N` folder the script tries, in order:

1. **Barcode** – decodes the EAN-13 barcode with pyzbar.
2. **ISBN OCR** – reads the printed ISBN number with Tesseract. Handles ISBN-13 and labelled ISBN-10 (older books), corrects common OCR misreads (`O`→`0`, `l`→`1`, …), and retries with a binarised image if the first pass fails.
3. **Cover OCR** – not yet implemented. It will reuse the OCR results from step 2 rather than reading the images again.

Photo orientation is corrected automatically from the phone's EXIF data.

## Output

One row per identified book, sorted by Title, with a frozen header and a filter on every column:

| Column | Meaning |
|---|---|
| Title, Authors, Year, Publisher, Language, ISBN-13 | Book metadata from the ISBN lookup |
| Source | `Barcode`, `ISBN OCR` or `Cover OCR` |
| Confidence | `High`, `Medium` or `Low` (below) |

| Confidence | When |
|---|---|
| High | Barcode decoded |
| Medium | ISBN read by OCR on the first pass, exactly as printed |
| Low | ISBN read by OCR only after binarising the image or correcting look-alike characters |

Low-confidence cells are highlighted so they are easy to check by hand. Filtering Confidence to `Low` gives a review list.

If the output file cannot be written (e.g. it is open in Excel on Windows), the catalogue is saved to a timestamped file next to it instead, e.g. `books_catalogue_20260922_141500.xlsx`.

If there is an odd number of photos, a warning is shown. Usually this means one photo is missing somewhere, which shifts every later pair by one. Barcode and ISBN identification still work in that case, because each pair still contains one back cover.
