"""安装经校验的 CPython WASI 构建，仅写入项目本机存储。"""
import argparse
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from zipfile import ZipFile

VERSION = "3.13.17"
ARCHIVE = f"python-{VERSION}-wasi_sdk-24.zip"
URL = f"https://github.com/brettcannon/cpython-wasi-build/releases/download/v{VERSION}/{ARCHIVE}"
SHA256 = "ceec2512d4bf2d2377a4791603e9879329c9b6aa0e7514a19ef827cdefe3b274"
MAX_ARCHIVE = 14_000_000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="使用已下载的发行压缩包。")
    args = parser.parse_args()

    if args.archive:
        with args.archive.open("rb") as source:
            data = source.read(MAX_ARCHIVE + 1)
    else:
        with urlopen(Request(URL, headers={"User-Agent": "VisionSoul runtime installer"}), timeout=60) as source:
            data = source.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("CPython WASI 压缩包与已固定的 SHA-256 不一致。")

    base = Path(__file__).resolve().parents[1] / "backend" / "storage" / "sandbox-runtime"
    base.mkdir(parents=True, exist_ok=True)
    target = base / VERSION
    if target.exists():
        raise SystemExit(f"运行时目录已存在：{target}")
    with TemporaryDirectory(prefix=".install-", dir=base) as temporary, ZipFile(BytesIO(data)) as archive:
        staging = Path(temporary) / VERSION
        staging.mkdir()
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            if item.filename not in {"LICENSE", "python.wasm"} and not item.filename.startswith("lib/"):
                continue
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise SystemExit("CPython WASI 压缩包包含不允许的路径。")
            archive.extract(item, staging)
        staging.rename(target)
    print(f"CPython WASI 已安装：{target}")


if __name__ == "__main__":
    main()
