import os
from PIL import Image
from pyzbar.pyzbar import decode
from isbnlib import meta
import pandas as pd
import xlsxwriter


path = "/Users/APDTaylor/Documents/Pithin/43700435-E522-4C07-AA21-D2DF0885A776_1_105_c.jpeg"

image = Image.open(path)
decoded = decode(image)
isbn_code = decoded.data.decode("utf-8")
isbn_data = meta(isbn_code)
print(isbn_data)

df = pd.DataFrame.from_dict(isbn_data)
output_path = "./output.csv"
df.to_csv(output_path, idex=False)