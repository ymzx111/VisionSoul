"""命令行和本机编程入口共用的源码提取与写入。"""
from datetime import date
from itertools import count
from pathlib import Path
import re


# 捕获文件名、代码块的语言和代码正文，支持反引号围栏以及中英文冒号。
CODE_BLOCK = re.compile(
    r"^(?:[ \t]*\[语言[ \t]*[:：][ \t]*(?P<declared>[^\]\n]+)\][ \t]*\n)?"
    r"[ \t]*\[文件名[ \t]*[:：][ \t]*(?P<filename>[^\]\n]+)\][ \t]*\n"
    r"(?:[ \t]*(?:\[(?:需要数据文件|输出文件[ \t]*[:：][^\]\n]+)\][ \t]*)?\n)*"
    r"[ \t]*(?P<fence>`{3,})[ \t]*(?P<language>[\w+#-]+)[ \t]*\n"
    r"(?P<code>[\s\S]*?)^[ \t]*(?P=fence)[ \t]*(?:\n|\Z)",
    re.MULTILINE,
)
EXTENSIONS = {
    "c": {".c"},
    "python": {".py"},
    "py": {".py"},
}
DECLARED_LANGUAGES = {"c语言": "c", "python语言": "python"}
DELIVERY_LANGUAGE_MESSAGE = "当前版本支持 Python 和 C，是否可以改用其中一种？"
_CPP_LABELS = {"cpp", "c++", "cc", "cxx", "cpp语言", "c++语言"}
_CPP_SUFFIXES = {".cpp", ".cc", ".cxx"}
FILE_DECLARATION = re.compile(r"^[ \t]*\[文件名[ \t]*[:：]", re.MULTILINE)
SAFE_FILENAME = re.compile(r"[\w-][\w .-]{0,119}")
WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", re.IGNORECASE)
# 模型漏写文件名时，用唯一代码块的语言推断文件名。
FENCE_BLOCK = re.compile(
    r"^[ \t]*(?P<fence>`{3,})[ \t]*(?P<language>[\w+#-]*)[ \t]*\n(?P<code>[\s\S]*?)^[ \t]*(?P=fence)[ \t]*(?:\n|\Z)",
    re.MULTILINE,
)
INFER_FILENAMES = {"python": "main.py", "py": "main.py", "c": "main.c"}
# 用户是否明确要代码；只在这类请求没有拿到源码时才提示，不打扰普通提问。
CODE_REQUEST = re.compile(r"代码|源码|程序|写出|编写|补全|填空|实现|(?:Python|C\+\+|C语言)\s*(?:编程)?题", re.IGNORECASE)


class UnsupportedDeliveryLanguage(ValueError):
    """C++ 源码暂不进入本机交付。"""


def parse_generated_code(text: str) -> tuple[str, str]:
    # 每次处理一份源码。保留原始缩进和空行，便于保存 Python 等语言的代码。
    text = text.replace("\r\n", "\n")
    blocks = list(CODE_BLOCK.finditer(text))
    if len(blocks) != 1:
        raise ValueError("请输入一份带 [文件名: main.c] 和完整代码块的回复。")

    block = blocks[0]
    filename = block["filename"].strip()
    code = block["code"]
    language = block["language"].lower()
    device_name = filename.split(".", 1)[0].rstrip()
    if not SAFE_FILENAME.fullmatch(filename) or WINDOWS_DEVICE.fullmatch(device_name):
        raise ValueError("请使用普通文件名，文件名中不能包含路径或设备名。")
    extension = Path(filename).suffix
    label = "".join((block["declared"] or "").lower().split())
    if extension.lower() in _CPP_SUFFIXES or language in _CPP_LABELS or label in _CPP_LABELS:
        raise UnsupportedDeliveryLanguage(DELIVERY_LANGUAGE_MESSAGE)
    if label:
        declared = DECLARED_LANGUAGES.get(label, label)
        if extension not in EXTENSIONS.get(declared, set()):
            raise ValueError("语言声明、代码块和文件扩展名须一致。")
    if extension not in EXTENSIONS.get(language, set()):
        raise ValueError("文件扩展名与代码块语言不匹配，目前只支持 Python 和 C。")
    if not code.strip():
        raise ValueError("代码块是空的。")

    return filename, code


def parse_generated_code_or_infer(text: str) -> tuple[str, str]:
    """先按标准声明解析；模型漏写文件名时，按唯一代码块的语言推断文件名。

    只在"整段回复里恰好一个带语言标签的代码块"时兜底，避免猜错多份源码。
    """
    try:
        return parse_generated_code(text)
    except ValueError as strict_error:
        normalized = text.replace("\r\n", "\n")
        if FILE_DECLARATION.search(normalized):
            raise
        blocks = list(FENCE_BLOCK.finditer(normalized))
        if len(blocks) != 1:
            raise strict_error
        block = blocks[0]
        language = block["language"].strip()
        if language.lower() in _CPP_LABELS:
            raise UnsupportedDeliveryLanguage(DELIVERY_LANGUAGE_MESSAGE) from strict_error
        filename = INFER_FILENAMES.get(language.lower())
        code = block["code"]
        if not filename or not code.strip() or "```" in code:
            raise strict_error
        declared = f"[文件名: {filename}]\n```{language}\n{code}```\n"
        return parse_generated_code(declared)


def source_directory(parent: Path, name: str) -> Path:
    """创建一个直接子目录，并拒绝目录重定向。"""
    directory = parent / name
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("源码保存目录必须是普通本机目录。")
    return directory


class UnsafeWorkspacePath(ValueError):
    """写入目标跳出了 workspace 目录。"""


def ensure_workspace_path(target: Path, workspace: Path) -> Path:
    """确认写入目标仍在 workspace 内；检测到 .. 或跳出就拦截报错。

    这是唯一的写盘入口校验：源码、修复版源码和沙箱本轮目录都必须先经过它。
    """
    target, workspace = Path(target), Path(workspace)
    if ".." in target.parts:
        raise UnsafeWorkspacePath("保存路径中不能包含 ..，已拦截。")
    resolved_workspace, resolved_target = workspace.resolve(), target.resolve()
    if resolved_target != resolved_workspace and resolved_workspace not in resolved_target.parents:
        raise UnsafeWorkspacePath(f"保存路径必须位于 {resolved_workspace} 目录内，已拦截：{target}")
    return resolved_target


def write_source(target: Path, code: str, *, encoding: str = "utf-8") -> Path:
    created = False
    try:
        # x 模式原子地检查文件是否已存在，防止覆盖用户已有源码。
        with target.open("x", encoding=encoding, newline="\n") as output:
            created = True
            output.write(code)
    except (OSError, UnicodeError):
        if created:
            target.unlink(missing_ok=True)
        raise
    return target


def save_dated_source(workspace: Path, filename: str, code: str, *, encoding: str = "utf-8") -> Path:
    """按语言保存源码，并为同日同名的结果分配序号。文件名需先通过提取校验。"""
    source = Path(filename)
    workspace = Path(workspace)
    if source.is_absolute() or ".." in source.parts:
        raise UnsafeWorkspacePath("源码文件名不能是绝对路径，也不能包含 ..，已拦截。")
    ensure_workspace_path(workspace, workspace)
    if source.suffix == ".py":
        folder = source_directory(workspace, "Python")
    elif source.suffix == ".c" or source.suffix in _CPP_SUFFIXES:
        folder = source_directory(workspace, "C")
    else:
        folder = workspace  # 保留命令行原有的其他语言支持。
    dated_stem = f"{date.today():%Y.%m.%d}.{source.stem}"
    for number in count(1):
        stem = dated_stem if number == 1 else f"{dated_stem}-{number}"
        target = folder / f"{stem}{source.suffix}"
        # 写盘前统一校验，文件名被拼出 .. 或绝对路径时直接拦截。
        ensure_workspace_path(target, workspace)
        try:
            return write_source(target, code, encoding=encoding)
        except FileExistsError:
            continue

