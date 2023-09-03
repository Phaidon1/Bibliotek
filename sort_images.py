import os
import shutil
import logging
import argparse

def create_subfolders_for_consecutive_pairs(folder_path,output_folder)->list:
    '''
    Sort images into subfolders.
    Two images per folder, with the assumption that for each book
    two pictures have been taken, one of the frontispice and one
    of the barcode or the isbn code.
    Return list of subfolders absolute paths.
    '''

    files = sorted(os.listdir(folder_path))
    # Could it be that the issue with the previous iteration is that there were
    # other files e.g. hidden system files?
    # The line below filters only images.
    files = [f for f in files if f.lower().endswith((".jpg",".jpeg",".png"))]
    
    subfolders = []
    
    for i in range(1, len(files), 2): #Why starting from 1 instead if 0?
        image1 = files[i]
        if i+1 < len(files):
            image2 = files[i + 1]

        else:
            image2 = None

        subfolder_name = f"Pair_{i//2 + 1}"
        subfolder_path = os.path.join(folder_path, subfolder_name)
        os.makedirs(subfolder_path, exist_ok=True)

        shutil.copy(os.path.join(folder_path, image1), os.path.join(subfolder_path, image1))
        if image2:
            shutil.copy(os.path.join(folder_path, image2), os.path.join(subfolder_path, image2))
        subfolders.append(subfolder_path)

        return subfolders
    
if __name__ == "__main__": 
    parser=argparse.ArgumentParser()
    parser.add_argument("-f","--folder",type=str,required=True,help="main folder with images")
    parser.add_argument("-o","--output",type=str,required=True,help="destination foler")
    parser.parse_args()
    folder = parser.folder
    output = parser.output
    create_subfolders_for_consecutive_pairs(folder,output)