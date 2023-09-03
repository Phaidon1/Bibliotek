# Bibliotek

Project Bibliotek:

Sorts images of front and back of books into pairs which are placed into folders.

These folders are then looked through by another script which scans for barcodes within these images.

Once found this code then scans the found barcode and prints the Title, Author and the publishing dates of the book

This information about the books is then printed in an excel spreacsheet, which will contain every book which has been photographed and is able to be listed by Author or Title

In this way a Digital library and archiving system is created

## Requirements 

Zbar - https://pypi.org/project/pyzbar/
Tesseract - https://github.com/tesseract-ocr/tesseract

### Zbar

Install [Zbar](https://pypi.org/project/pyzbar/) to be able to read barcodes

OsX
```
brew install zbar

```
Linux
```
sudo apt-get install libzbar0
```
### Tesseract 

Install [Tesseract](https://github.com/tesseract-ocr/tesseract) in order to scan the images for text

Linux
```
sudo apt install tesseract-ocr-all
```
Mac 
```
brew install tesseract --all-languages

```

## The Scripts

### Sorting Images in Folders
```
usage: sort_images.py [-h] -f FOLDER -o OUTPUT

options:
  -h, --help            show this help message and exit
  -f FOLDER, --folder FOLDER
                        main folder with images
  -o OUTPUT, --output OUTPUT
                        destination foler
```
### Scanning Barcodes and Text 



