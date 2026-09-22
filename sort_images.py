import os
import shutil
import logging

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")


def create_subfolders_for_consecutive_pairs(folder_path, output_folder) -> list:
    '''
    Sort images into subfolders.
    Two images per folder, with the assumption that for each book
    two pictures have been taken, one of the frontispice and one
    of the barcode or the isbn code.
    Images are copied into `output_folder`, not into `folder_path`.
    Return list of subfolders absolute paths.
    '''

    os.makedirs(output_folder, exist_ok=True)

    files = sorted(os.listdir(folder_path))
    # Filter to only image files, so hidden/system files don't throw off pairing.
    files = [f for f in files if f.lower().endswith(IMAGE_EXTENSIONS)]

    if not files:
        logger.warning("No images found in %s", folder_path)
        return []

    if len(files) % 2:
        # An odd count usually means a photo is missing somewhere in the middle,
        # not just at the end. Every pair after that point is then offset by one
        # (back of book N + front of book N+1). Barcode/ISBN identification still
        # works because each pair still holds one back cover, but it's worth knowing.
        logger.warning(
            "Odd number of images (%d): a photo may be missing, which would offset "
            "every pair after it. The last image will be scanned on its own.",
            len(files),
        )

    subfolders = []

    # Start at 0 so the first image is included. Step by 2 to take consecutive pairs.
    for i in range(0, len(files), 2):
        image1 = files[i]
        image2 = files[i + 1] if i + 1 < len(files) else None

        pair_number = i // 2 + 1
        subfolder_name = f"Pair_{pair_number}"
        subfolder_path = os.path.join(output_folder, subfolder_name)
        os.makedirs(subfolder_path, exist_ok=True)

        shutil.copy(os.path.join(folder_path, image1), os.path.join(subfolder_path, image1))
        if image2:
            shutil.copy(os.path.join(folder_path, image2), os.path.join(subfolder_path, image2))
        else:
            logger.warning("%s has no pair; placed alone in %s", image1, subfolder_name)

        subfolders.append(subfolder_path)
        logger.info("Created %s with %s", subfolder_path, [image1, image2] if image2 else [image1])

    return subfolders
