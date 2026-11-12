# 将一段带文件名声明和 Markdown 代码块的 AI 回复保存到本机。
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.source_files import parse_generated_code, save_dated_source, source_directory


def save_generated_code(text: str) -> Path:
    filename, code = parse_generated_code(text)
    workspace = source_directory(Path.cwd().resolve(), "workspace")
    return save_dated_source(workspace, filename, code)


def main() -> int:
    parser = argparse.ArgumentParser(description="提取 AI 回复中的代码，按语言和日期保存到当前目录的 workspace。")
    parser.add_argument("reply", nargs="?", type=Path, help="保存了 AI 回复的 UTF-8 文本文件，省略时从标准输入读取。")
    args = parser.parse_args()

    try:
        raw = args.reply.read_bytes() if args.reply else sys.stdin.buffer.read()
        text = raw.decode("utf-8-sig")
        target = save_generated_code(text)
    except FileExistsError as error:
        print(f"保存失败，文件或目录已存在，未覆盖：{error.filename}", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print(f"保存失败：{error}", file=sys.stderr)
        return 1

    print(f"保存成功：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
