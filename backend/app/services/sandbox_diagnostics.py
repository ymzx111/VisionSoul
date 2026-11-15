"""Classify sandbox failures and locate lines supported by the diagnostics."""
from difflib import SequenceMatcher
import re

from app.services.python_sandbox import PythonCheck

PYTHON_LINE = re.compile(r'^\s*File "[/\\]*program\.py", line (\d+)', re.MULTILINE)
PYTHON_PREFLIGHT = re.compile(r"^第 (\d+) 行：")
NATIVE_LINE = re.compile(r"\bmain\.(?:c|cpp):(\d+)(?::\d+)?: (?:fatal )?error:")
INPUT_ERROR = re.compile(r"ValueError: (?:invalid literal for int|could not convert string to float)")
SYNTAX_ERROR = re.compile(r"^(?:SyntaxError|IndentationError|TabError):", re.MULTILINE)


def diagnose_run(check: PythonCheck, source: str, language: str, runtime_source: str | None = None) -> PythonCheck:
    if check.status == "mismatch":
        label = "输出与预期不一致，需核对题意、代码和预期答案。"
    elif check.status in {"awaiting_file", "awaiting_input", "file_unused"}:
        return check
    elif check.status == "failed" and (check.phase == "compile" or
                                       (language == "python" and SYNTAX_ERROR.search(check.stderr))):
        label = "代码编译或语法检查失败。"
    elif check.status == "failed" and language == "python" and INPUT_ERROR.search(check.stderr):
        label = "数据转换失败，请核对输入格式和代码读取规则。"
    elif check.status == "failed":
        label = "程序运行失败，请查看错误详情。"
    else:
        return check

    updates = {"detail": f"{label} {check.detail}"}
    if check.status == "failed" and (check.attempts <= 1 or check.repaired_code):
        if language == "python":
            lines = PYTHON_LINE.findall(check.stderr) or PYTHON_PREFLIGHT.findall(check.stderr)
            line = int(lines[-1]) if lines else None
        else:
            lines = NATIVE_LINE.findall(check.stderr)
            line = int(lines[0]) if lines else None
        original = (check.repaired_code or source).splitlines()
        if line and runtime_source is not None and runtime_source != source:
            generated = runtime_source.splitlines()
            blocks = SequenceMatcher(None, [row.strip() for row in generated],
                                     [row.strip() for row in original]).get_matching_blocks()
            line = next((other + line - start for start, other, length in blocks
                         if start <= line - 1 < start + length), None)
        if line and 1 <= line <= len(original):
            updates.update(error_line=line, source_line=original[line - 1][:500])
    return check.model_copy(update=updates)
