'''
Bibliotek command line.

    python bibliotek.py sort -f PHOTOS [-o SORTED]
    python bibliotek.py scan -f SORTED [-n NOT_SCANNED] [-o CATALOGUE.xlsx]
    python bibliotek.py run  -f PHOTOS [-n NOT_SCANNED] [-o CATALOGUE.xlsx] [--keep-pairs DIR]

`run` does sort + scan in one go. `sort` and `scan` stay separate so an
already-sorted folder can be re-scanned (e.g. after tuning the OCR) without
re-sorting.
'''
import os
import sys
import logging
import argparse
import tempfile

from sort_images import create_subfolders_for_consecutive_pairs
from scan_images import scan_all_folders, list_pair_folders

logger = logging.getLogger("bibliotek")


def _abs(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _sibling(folder: str, suffix: str) -> str:
    '''~/Pictures/books + "_sorted" -> ~/Pictures/books_sorted'''
    parent, name = os.path.split(_abs(folder))
    return os.path.join(parent, name + suffix)


def _ensure_xlsx(path: str) -> str:
    if not path.lower().endswith(".xlsx"):
        logger.warning("Output path %s does not end in .xlsx; appending extension", path)
        path += ".xlsx"
    return path


def _require_folder(path: str, what: str) -> bool:
    if not os.path.isdir(path):
        logger.error("%s folder not found: %s", what, path)
        return False
    return True


def _scan(pairs_folder: str, not_scanned: str, output: str) -> int:
    subfolders = list_pair_folders(pairs_folder)
    if not subfolders:
        logger.error("No pair folders found in %s", pairs_folder)
        return 1
    written, found, missed = scan_all_folders(subfolders, not_scanned, output)
    print(f"\nIdentified {found} of {found + missed} books.")
    print(f"Catalogue:      {written}")
    if missed:
        print(f"Not identified: {not_scanned}")
    return 0


def cmd_sort(args) -> int:
    photos = _abs(args.folder)
    if not _require_folder(photos, "Photo"):
        return 1
    output = _abs(args.output) if args.output else _sibling(photos, "_sorted")
    pairs = create_subfolders_for_consecutive_pairs(photos, output)
    print(f"\nCreated {len(pairs)} pair folders in {output}")
    return 0 if pairs else 1


def cmd_scan(args) -> int:
    sorted_folder = _abs(args.folder)
    if not _require_folder(sorted_folder, "Sorted"):
        return 1
    not_scanned = _abs(args.not_scanned_folder) if args.not_scanned_folder else _sibling(sorted_folder, "_not_scanned")
    output = _ensure_xlsx(_abs(args.output) if args.output else _sibling(sorted_folder, "_catalogue.xlsx"))
    return _scan(sorted_folder, not_scanned, output)


def cmd_run(args) -> int:
    photos = _abs(args.folder)
    if not _require_folder(photos, "Photo"):
        return 1
    not_scanned = _abs(args.not_scanned_folder) if args.not_scanned_folder else _sibling(photos, "_not_scanned")
    output = _ensure_xlsx(_abs(args.output) if args.output else _sibling(photos, "_catalogue.xlsx"))

    if args.keep_pairs:
        pairs_folder = _abs(args.keep_pairs)
        if os.path.isdir(pairs_folder) and os.listdir(pairs_folder):
            # Sorting into a folder that already has Pair_N folders would mix old
            # and new photos in the same pair.
            logger.error("--keep-pairs folder %s is not empty; choose a new or empty folder", pairs_folder)
            return 1
        if not create_subfolders_for_consecutive_pairs(photos, pairs_folder):
            return 1
        return _scan(pairs_folder, not_scanned, output)

    # Default: pairs go in a temporary folder. They are only copies of the
    # originals, and unidentified pairs are moved to not_scanned before the
    # folder is deleted, so nothing is lost.
    with tempfile.TemporaryDirectory(prefix="bibliotek_pairs_") as pairs_folder:
        if not create_subfolders_for_consecutive_pairs(photos, pairs_folder):
            return 1
        return _scan(pairs_folder, not_scanned, output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bibliotek",
        description="Catalogue a book collection from photos (cover + back per book).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sort = sub.add_parser("sort", help="pair consecutive photos into Pair_N folders")
    p_sort.add_argument("-f", "--folder", required=True, help="folder of photos, two per book")
    p_sort.add_argument("-o", "--output", help="destination folder (default: <folder>_sorted)")
    p_sort.set_defaults(func=cmd_sort)

    p_scan = sub.add_parser("scan", help="identify books in already-sorted Pair_N folders")
    p_scan.add_argument("-f", "--folder", required=True, help="folder containing Pair_N subfolders")
    p_scan.add_argument("-n", "--not-scanned-folder", help="where unidentified pairs are moved (default: <folder>_not_scanned)")
    p_scan.add_argument("-o", "--output", help="output .xlsx catalogue (default: <folder>_catalogue.xlsx)")
    p_scan.set_defaults(func=cmd_scan)

    p_run = sub.add_parser("run", help="sort and scan in one step")
    p_run.add_argument("-f", "--folder", required=True, help="folder of photos, two per book")
    p_run.add_argument("-n", "--not-scanned-folder", help="where unidentified pairs are moved (default: <folder>_not_scanned)")
    p_run.add_argument("-o", "--output", help="output .xlsx catalogue (default: <folder>_catalogue.xlsx)")
    p_run.add_argument("--keep-pairs", metavar="DIR", help="keep the Pair_N folders in DIR instead of a temporary folder")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
