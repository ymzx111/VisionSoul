"""Persist a declared text result from a completed sandbox run."""
import ast
from collections import Counter
from pathlib import Path
import re
from tempfile import mkdtemp

from app.services.python_sandbox import PythonCheck
from app.services.source_files import (
    FENCE_BLOCK, SAFE_FILENAME, WINDOWS_DEVICE, ensure_workspace_path, source_directory, write_source,
)

DECLARATION = re.compile(r"^[ \t]*\[输出文件\s*[:：]\s*([^\]\r\n]+)\][ \t]*$", re.MULTILINE)
CONTENT = re.compile(r"^\[VISIONSOUL_OUTPUT_BEGIN\]\r?\n(.*?)^\[VISIONSOUL_OUTPUT_(END|END_EXACT)\](?:\r?\n|\Z)",
                     re.MULTILINE | re.DOTALL)
MARKERS = re.compile(r"^\[VISIONSOUL_OUTPUT_(?:BEGIN|END|END_EXACT)\]\r?$", re.MULTILINE)
NO_FILE = re.compile(r"^\[VISIONSOUL_NO_OUTPUT_FILE\](?:\r?\n|\Z)", re.MULTILINE)
PYTHON_OUTPUT_CONTEXT = r'''
from contextlib import contextmanager as _visionsoul_contextmanager
from io import StringIO as _visionsoul_StringIO

@_visionsoul_contextmanager
def _visionsoul_text_output(filename, expected):
    if filename != expected:
        raise ValueError("TXT 输出文件名与工作台识别的文件名不一致")
    with _visionsoul_StringIO() as buffer:
        yield buffer
        print("[VISIONSOUL_OUTPUT_BEGIN]")
        print(buffer.getvalue())
        print("[VISIONSOUL_OUTPUT_END_EXACT]")
'''


def python_output_constants(tree: ast.Module) -> dict[str, str]:
    """Read simple module-level string assignments that appear once."""
    assignments = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if (isinstance(target, ast.Name) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            assignments[target.id] = node.value.value
    stores = Counter(node.id for node in ast.walk(tree)
                     if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)))
    return {name: value for name, value in assignments.items() if stores[name] == 1}


def python_text_output(code: str, declared: str | None = None) -> tuple[str, str | None]:
    """Handle a single fixed text file opened with Python's with statement."""
    if "_visionsoul_" in code:
        return code, declared
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, declared
    constants = python_output_constants(tree)
    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "open"
                    and len(call.args) == 2 and isinstance(call.args[1], ast.Constant)
                    and call.args[1].value in ("w", "wt")
                    and all(kw.arg == "encoding" and isinstance(kw.value, ast.Constant)
                            and kw.value.value in ("utf-8", "utf8", "UTF-8") for kw in call.keywords)):
                path = call.args[0]
                filename = path.value if isinstance(path, ast.Constant) else (
                    constants.get(path.id) if isinstance(path, ast.Name) else None)
                if isinstance(filename, str):
                    writes.append((item, path, filename))
    if len(writes) != 1:
        return code, declared
    item, path, filename = writes[0]
    if not valid_text_output(filename) or (declared and filename != declared):
        return code, declared
    if "[VISIONSOUL_OUTPUT_" in code:
        return code, filename
    item.context_expr = ast.Call(func=ast.Name(id="_visionsoul_text_output", ctx=ast.Load()),
                                 args=[path, ast.Constant(filename)], keywords=[])
    start = int(ast.get_docstring(tree, clean=False) is not None)
    while (start < len(tree.body) and isinstance(tree.body[start], ast.ImportFrom)
           and tree.body[start].module == "__future__"):
        start += 1
    tree.body[start:start] = ast.parse(PYTHON_OUTPUT_CONTEXT).body
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n", filename
# Skip comments and literals so quoted sample code cannot become an output target.
C_TEXT_STREAM = re.compile(
    r'(?P<skip>//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r'|\b(?P<stream>[A-Za-z_]\w*)\s*=\s*(?P<open>fopen\s*\(\s*'
    r'"(?P<filename>[\w-][\w .-]{0,115}\.txt)"\s*,\s*"w[bt]?"\s*\))'
    r'|(?P<close>\bfclose\s*\(\s*(?P<closed>[A-Za-z_]\w*)\s*\))',
    re.IGNORECASE,
)


def c_text_output(code: str, declared: str | None = None) -> tuple[str, str | None]:
    """Route a single fixed C-style text output through the existing bounded stdout."""
    if "[VISIONSOUL_OUTPUT_BEGIN]" in code or "[VISIONSOUL_OUTPUT_END]" in code:
        return code, declared
    matches = list(C_TEXT_STREAM.finditer(code))
    openings = [match for match in matches if match["open"]]
    if len(openings) != 1:
        return code, declared
    opening = openings[0]
    filename = opening["filename"]
    if ((declared and declared != filename)
            or WINDOWS_DEVICE.fullmatch(filename.split(".", 1)[0].rstrip())):
        return code, declared
    closures = [match for match in matches if match.start() > opening.end()
                and match["closed"] == opening["stream"]]
    if len(closures) != 1:
        return code, declared
    start, end = opening.span("open")
    close_start, close_end = closures[0].span("close")
    # Only the Wasm compilation sees these changes. The generated IDE source
    # continues using fopen/fclose, and all guest directories stay read-only.
    return (
        code[:start] + '(fputs("[VISIONSOUL_OUTPUT_BEGIN]\\n", stdout), stdout)'
        + code[end:close_start]
        + '(fputs("\\n[VISIONSOUL_OUTPUT_END_EXACT]\\n", stdout) == EOF ? EOF : 0)'
        + code[close_end:],
        filename,
    )


def declared_text_output(reply: str) -> str | None:
    prose = FENCE_BLOCK.sub("", reply.replace("\r\n", "\n"))
    matches = DECLARATION.findall(prose)
    if len(matches) != 1:
        return None
    filename = matches[0].strip()
    return filename if valid_text_output(filename) else None


def valid_text_output(filename: str) -> bool:
    return bool(SAFE_FILENAME.fullmatch(filename)
                and not WINDOWS_DEVICE.fullmatch(filename.split(".", 1)[0].rstrip())
                and Path(filename).suffix.lower() == ".txt")


def visible_output(raw: str) -> str:
    stdout = NO_FILE.sub("", CONTENT.sub("", raw))
    if marker := MARKERS.search(stdout):
        stdout = stdout[:marker.start()]
    return stdout


def save_sandbox_output(workspace: Path, filename: str | None, check: PythonCheck) -> PythonCheck:
    raw = check.stdout
    stdout = visible_output(raw)
    updates = {"stdout": stdout}
    if check.case_results:
        updates["case_results"] = [row.model_copy(update={"stdout": visible_output(row.stdout)})
                                   for row in check.case_results]
    check = check.model_copy(update=updates)
    if not filename or check.status not in {"ran", "passed"} or check.case_source == "model":
        return check
    if check.case_results:
        return check.model_copy(update={"detail": f"{check.detail} · 要生成结果文件，请单独试跑一次。"})
    blocks = list(CONTENT.finditer(raw))
    if not MARKERS.search(raw) and len(NO_FILE.findall(raw)) == 1:
        return check.model_copy(update={"detail": f"{check.detail} 本轮按题目条件未生成「{filename}」。"})
    if len(blocks) != 1 or len(MARKERS.findall(raw)) != 2:
        return check.model_copy(update={"status": "failed",
                                        "detail": f"程序已运行，但未提供完整的「{filename}」内容。"})
    block = blocks[0]
    content = block.group(1)
    if block.group(2) == "END_EXACT":
        content = content.removesuffix("\n")
    try:
        root = source_directory(workspace.parent.resolve(), workspace.name)
        outputs = source_directory(root, "outputs")
        folder = Path(mkdtemp(prefix="run-", dir=outputs))
        target = folder / filename
        ensure_workspace_path(target, root)
        write_source(target, content)
    except (OSError, ValueError):
        return check.model_copy(update={"status": "failed", "stdout": stdout,
                                        "detail": f"程序已运行，但「{filename}」保存失败，请检查工作区权限。"})
    return check.model_copy(update={"stdout": stdout, "output_path": str(target)})
