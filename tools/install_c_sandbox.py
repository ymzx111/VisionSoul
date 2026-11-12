"""安装经校验的 Zig 工具链，仅写入项目本机存储。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe .\\tools\\install_c_sandbox.py
    .\\.venv\\Scripts\\python.exe .\\tools\\install_c_sandbox.py --archive 已下载的zip
"""
import argparse
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import time
from urllib.request import Request, urlopen
from zipfile import ZipFile

VERSION = "0.17.1"
ARCHIVE = f"zig-x86_64-windows-{VERSION}.zip"
URL = f"https://ziglang.org/download/{VERSION}/{ARCHIVE}"
SHA256 = "b03f477a1943673e02aca0f570e8030dffd536ce1f45cbc4d3326ec31159562e"
MAX_ARCHIVE = 130_000_000
# 只保留编译需要的部分，跳过体积很大的 doc/。
KEEP = {"zig.exe", "LICENSE", "README.md"}


def fetch(archive: Path | None, cache: Path) -> bytes:
    if archive:
        data = archive.read_bytes()
    elif cache.is_file() and len(data := cache.read_bytes()) <= MAX_ARCHIVE:
        print(f"使用已缓存的压缩包：{cache}")
    else:
        request = Request(URL, headers={"User-Agent": "VisionSoul toolchain installer"})
        with urlopen(request, timeout=180) as source:
            data = source.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise SystemExit("Zig 压缩包超过预期大小，已停止。")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("Zig 压缩包与已固定的 SHA-256 不一致，已停止。")
    return data


def extract(data: bytes, staging: Path) -> None:
    """按白名单逐条写出，去掉压缩包顶层目录，不做跨目录改名。"""
    staging.mkdir(parents=True, exist_ok=True)
    with ZipFile(BytesIO(data)) as archive:
        for item in archive.infolist():
            parts = PurePosixPath(item.filename).parts
            if (not parts or any(part in {"", ".", ".."} for part in parts)
                    or item.filename.startswith("/") or "\\" in item.filename):
                raise SystemExit("Zig 压缩包包含不允许的路径。")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise SystemExit("Zig 压缩包包含符号链接。")
            if len(parts) < 2:
                continue
            relative = PurePosixPath(*parts[1:])
            if relative.parts[0] not in KEEP and relative.parts[0] != "lib":
                continue
            destination = staging.joinpath(*relative.parts)
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def move_into_place(staging: Path, target: Path) -> None:
    """目录改名偶尔被杀软短暂占用；重试几次后改用复制。"""
    for attempt in range(5):
        try:
            staging.rename(target)
            return
        except PermissionError:
            if attempt == 4:
                break
            time.sleep(0.6)
    shutil.copytree(staging, target)
    shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="使用已下载的发行压缩包。")
    args = parser.parse_args()

    base = Path(__file__).resolve().parents[1] / "backend" / "storage" / "sandbox-runtime" / "c"
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    target = base / f"zig-{VERSION}"
    if (target / "zig.exe").is_file():
        print(f"Zig 已安装：{target}")
        return target

    data = fetch(args.archive, base / ARCHIVE)
    staging = base / f".install-{VERSION}"
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        extract(data, staging)
        move_into_place(staging, target)
    finally:
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)

    zig = target / "zig.exe"
    if not zig.is_file():
        raise SystemExit("安装后未找到 zig.exe。")
    result = subprocess.run([str(zig), "version"], capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or result.stdout.strip() != VERSION:
        raise SystemExit(f"Zig 版本校验失败：{result.stdout!r} {result.stderr!r}")
    size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    print(f"Zig 已安装：{target}（版本 {VERSION}，占用 {size / 1048576:.0f} MB）")
    return target


if __name__ == "__main__":
    main()
