import os
import shutil
from PIL import Image
from pyzbar.pyzbar import decode
from isbnlib import meta
import pandas as pd
import logging
from pandas import DataFrame 
import re

def data_from_barcode(images: list(Image)) -> DataFrame:
    '''
    Find all image files in subfolder and if a barcode is
    detected and read, build and output a dataframe.
    If no barcode, then ocr scan for isbn code.
    If no isbn to read, then try ocr of the cover page.
    If no luck, then move to a folder to read book data manually.
    '''

    for image in images:
        # Work on the images, not the image paths
        # The idea is to open the images once for the whole subfolder workflow to save time

        #TODO
        ## The function below was not part of the script
        ## Either paste here or create helpers file and import
        isbn_data = apply_barcode_scanner_to_image(image) # Image, not image path
        
        if isbn_data:
            df = DataFrame.from_dict(isbn_data)
            return df # close the function as soon as it finds a valid barcode
            #output_path = os.path.join(subfolder_path, "output.csv")
            #df.to_csv(output_path, index=False)
        
    # If at the end of the for loop there's no barcode, then return an empty dataframe
    return DataFrame()

def data_from_isbn_ocr(images: list(Image)) -> DataFrame:
    '''
    After populating this function add description here
    '''
    #TODO
    ## Populate this one
    return DataFrame()

def data_from_cover_ocr(images: list(Image)) -> DataFrame:
    '''
    After populating this function add description here
    '''
    #TODO
    ## Populate this one
    return DataFrame()

def get_book_data(subfolder: str) -> DataFrame:
    '''
    After populating this function add description here
    '''
    images = [Image(f) for f in os.listdir(subfolder) if f.lower().endswith((".jpg",".jpeg",".png"))]
    data = data_from_barcode(images)
    if data.empty:
        data = data_from_isbn_ocr(images)
    if data.empty:
        # Plan C (the difficult one) if the cover needs to be scanned completely for title, author etc
        data = data_from_cover_ocr(images)
    
    return DataFrame

def scan_all_folders(subfolders: str, not_scanned_folder: str, output_csv: str):
    '''
    Add description
    '''
    data_all_books = DataFrame()
    for subfolder in subfolders:
        book_data = get_book_data(subfolder)
        if book_data.empty == False:
            data_all_books = pd.concat([data_all_books, book_data], axis=0)
        else:
            # Move files to not_scanned_folder to process them manually
            pass

    data_all_books.to_csv(output_csv)

if __name__ == "_main_": # Syntax requires double underscore (__ instad of _)

    #TODO
    ## Separation between code and data.
    ## Images to be moved a folder that is not the repository folder.

    #TODO
    ## Arguments to be passed via command line (import argparse)

    #TODO
    ## Implement logging

    folder_path = "/Users/APDTaylor/Documents/Documents2/Bibliotek/Sorted_Images"
    not_scanned_folder = "/Users/APDTaylor/Documents/Documents2/Bibliotek/not_scanned"
    output_csv = "/Users/APDTaylor/Documents/Documents2/Bibliotek/catalogue.csv"   
    
    subfolders = create_subfolders_for_consecutive_pairs(folder_path)
    
    try:
        os.mkdir(not_scanned_folder)
    except FileExistsError:
        pass

    scan_all_folders(subfolders, not_scanned_folder, output_csv)