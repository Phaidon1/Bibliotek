# Bibliotek

Project Bibliotek:

Sorts images of the front and back of books into pairs, which are placed into folders.

These folders are then looked through by another script, which scans for barcodes within these images. If no barcode can be read, it falls back to reading the printed ISBN number with OCR.

Once an ISBN is found, the script looks up the Title, Author, Publisher and publishing year of the book.

This information is written to an Excel spreadsheet containing every book that has been photographed and identified. The sheet has a filter on every column, so it can be sorted by Author, Title, Year, etc.

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

## The Scripts

### Sorting Images in Folders

Photos must be taken two per book (cover, then back/barcode), so that sorting the files by name puts each book's photos next to each other.

```
usage: sort_images.py [-h] -f FOLDER -o OUTPUT

options:
  -h, --help            show this help message and exit
  -f FOLDER, --folder FOLDER
                        main folder with images
  -o OUTPUT, --output OUTPUT
                        destination folder
```

Example:
```
python sort_images.py -f ~/Pictures/books -o ~/Pictures/books_sorted
```

### Scanning Barcodes and Text

```
usage: scan_images.py [-h] -f FOLDER -n NOT_SCANNED_FOLDER -o OUTPUT

options:
  -h, --help            show this help message and exit
  -f FOLDER, --folder FOLDER
                        folder containing sorted Pair_N subfolders (output of sort_images.py)
  -n NOT_SCANNED_FOLDER, --not-scanned-folder NOT_SCANNED_FOLDER
                        destination folder for pairs that could not be identified
  -o OUTPUT, --output OUTPUT
                        output .xlsx catalogue file
```

Example:
```
python scan_images.py -f ~/Pictures/books_sorted -n ~/Pictures/books_unidentified -o library.xlsx
```

For each `Pair_N` folder the script tries, in order:

1. **Barcode** – decodes the EAN-13 barcode with pyzbar.
2. **ISBN OCR** – reads the printed ISBN number with Tesseract. Handles ISBN-13 and labelled ISBN-10 (older books), corrects common OCR misreads (`O`→`0`, `l`→`1`, …), and retries with a binarised image if the first pass fails.
3. **Cover OCR** – not yet implemented.

Photo orientation is corrected automatically from the phone's EXIF data.

Output:

- `OUTPUT.xlsx` – one row per identified book (Title, Authors, Year, Publisher, Language, ISBN-13), sorted by Title, with a frozen header and a filter on every column.
- `NOT_SCANNED_FOLDER` – pairs that could not be identified are **moved** here for manual review.

If the output file cannot be written (e.g. it is open in Excel on Windows), the catalogue is saved to a timestamped file next to it instead, e.g. `library_20260922_141500.xlsx`.
