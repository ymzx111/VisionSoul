"""Install a pinned YoWASP Clang/LLD build inside ignored project storage."""
import argparse
from hashlib import file_digest
from pathlib import Path
import shutil
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from uuid import uuid4

VERSION = "22.0.0-git20542-10"
ARCHIVE = f"clang-{VERSION}.tgz"
URL = f"https://registry.npmjs.org/@yowasp/clang/-/{ARCHIVE}"
SHA256 = "6230ea1afa9691fa065935cf68c01642ff9b31c183fe8ac64cdfda025df06009"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1] / "backend/storage/sandbox-runtime/c"
    base.mkdir(parents=True, exist_ok=True)
    target = base / "yowasp"
    if target.exists():
        print(f"Compiler directory already exists: {target}")
        return
    with TemporaryDirectory(prefix="visionsoul-toolchain-") as scratch:
        archive = args.archive or Path(scratch) / ARCHIVE
        if args.archive is None:
            with urlopen(URL, timeout=120) as source, archive.open("wb") as output:
                shutil.copyfileobj(source, output)
        with archive.open("rb") as file:
            if file_digest(file, "sha256").hexdigest() != SHA256:
                raise SystemExit("YoWASP archive SHA-256 mismatch")
        staging = base / f".install-{uuid4().hex}"
        staging.mkdir()
        try:
            with tarfile.open(archive, "r:gz") as package:
                package.extractall(staging, filter="data")
            if not (staging / "package/gen/bundle.js").is_file():
                raise SystemExit("YoWASP archive is incomplete")
            staging.rename(target)
        finally:
            if staging.is_dir() and staging.resolve().parent == base.resolve():
                shutil.rmtree(staging)
    print(target)


if __name__ == "__main__":
    main()
