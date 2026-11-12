"""命令行和本机编程入口共用的源码提取与写入。"""
from pathlib import Path
import re


# 捕获文件名、代码块的语言和代码正文，支持反引号围栏以及中英文冒号。
CODE_BLOCK = re.compile(
    r"^(?:[ \t]*\[语言[ \t]*[:：][ \t]*(?P<declared>[^\]\n]+)\][ \t]*\n)?"
    r"[ \t]*\[文件名[ \t]*[:：][ \t]*(?P<filename>[^\]\n]+)\][ \t]*\n"
    r"(?:[ \t]*\n)*[ \t]*(?P<fence>`{3,})[ \t]*(?P<language>[\w+#-]+)[ \t]*\n"
    r"(?P<code>[\s\S]*?)^[ \t]*(?P=fence)[ \t]*(?:\n|\Z)",
    re.MULTILINE,
)
EXTENSIONS = {
    "c": {".c"},
    "cpp": {".cpp", ".cc", ".cxx"},
    "c++": {".cpp", ".cc", ".cxx"},
    "python": {".py"},
    "py": {".py"},
    "csharp": {".cs"},
    "cs": {".cs"},
    "c#": {".cs"},
}
DECLARED_LANGUAGES = {"c语言": "c", "c++语言": "cpp", "python语言": "python", "c#语言": "cs"}
FILE_DECLARATION = re.compile(r"^[ \t]*\[文件名[ \t]*[:：]", re.MULTILINE)
SAFE_FILENAME = re.compile(r"[\w-][\w .-]{0,119}")
WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", re.IGNORECASE)


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
    if block["declared"]:
        label = "".join(block["declared"].lower().split())
        declared = DECLARED_LANGUAGES.get(label, label)
        if extension not in EXTENSIONS.get(declared, set()):
            raise ValueError("语言声明、代码块和文件扩展名须一致。")
    if extension not in EXTENSIONS.get(language, set()):
        raise ValueError("文件扩展名与代码块语言不匹配，支持 C、C++、C# 和 Python。")
    if not code.strip():
        raise ValueError("代码块是空的。")

    return filename, code


def source_directory(parent: Path, name: str) -> Path:
    """创建一个直接子目录，并拒绝目录重定向。"""
    directory = parent / name
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("源码保存目录必须是普通本机目录。")
    return directory


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

