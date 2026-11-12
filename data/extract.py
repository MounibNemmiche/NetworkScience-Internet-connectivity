import bz2
import gzip
import shutil
from pathlib import Path

def extract_single_gz(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Extracting: {input_path.name} -> {output_path.name}")

    try:
        with gzip.open(input_path, "rb") as source_stream:
            with open(output_path, "wb") as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream)
        print(f"Successful extraction: {output_path.resolve()}")
    except gzip.BadGzipFile:
        print(f"Error: '{input_path.name}' is not a valid gzip file or is corrupted.")
        if output_path.exists():
            output_path.unlink()
    except Exception as exc:
        print(f"Extraction error: {exc}")
        if output_path.exists():
            output_path.unlink()

def extract_single_bz2(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Extracting: {input_path.name} -> {output_path.name}")

    try:
        with bz2.open(input_path, "rb") as source_stream:
            with open(output_path, "wb") as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream)
        print(f"Successful extraction: {output_path.resolve()}")
    except OSError as exc:
        print(f"Error: '{input_path.name}' is not a valid bzip2 file or is corrupted ({exc}).")
        if output_path.exists():
            output_path.unlink()
    except Exception as exc:
        print(f"Extraction error: {exc}")
        if output_path.exists():
            output_path.unlink()

def main():
    base_dir = Path(__file__).resolve().parent
    raw_dir = base_dir / "raw"
    processed_dir = base_dir / "processed" / "v1"

    compressed_gz_name = "20261001.as-org2info.txt.gz"
    compressed_bz2_name = "20261001.as-rel2.txt.bz2"

    source_path_gz = raw_dir / compressed_gz_name
    source_path_bz2 = raw_dir / compressed_bz2_name

    destination_path_gz = processed_dir / source_path_gz.stem
    destination_path_bz2 = processed_dir / source_path_bz2.stem

    if not source_path_gz.exists():
        print(f"Error: source file '{source_path_gz}' does not exist.")
        return

    if not source_path_bz2.exists():
        print(f"Error: source file '{source_path_bz2}' does not exist.")
        return

    extract_single_gz(source_path_gz, destination_path_gz)
    extract_single_bz2(source_path_bz2, destination_path_bz2)

if __name__ == "__main__":
    main()