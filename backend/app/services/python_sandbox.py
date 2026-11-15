"""启动一次性 WASI Python 测试，校验输出并清理本轮文件。

约定：
- 自动运行只发生在 WASI 沙箱内；沙箱不可用就停止。
- 验收样例支持多组用例，每组独立运行、逐组判定。
- 试跑失败或输出不符时，最多回传 3 次结构化错误让模型修复。
- 带验收样例时只回传异常类型与行号，stdout/stderr 不出本机。
"""
import ast
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings
from app.services.deepseek import DeepSeekClient, DeepSeekError, TokenUsage, add_usage
from app.services.sandbox_output import matches_screen_output
from app.services.source_files import (
    SAFE_FILENAME, WINDOWS_DEVICE, ensure_workspace_path, parse_generated_code, source_directory,
)

logger = logging.getLogger(__name__)
RUN_LOCK = Lock()
WORKER = Path(__file__).with_name("python_wasi_worker.py")
MAX_CASES = 8
MAX_REPAIR_ATTEMPTS = 3
AUTOFIX_STATUSES = {"failed", "mismatch"}
REPAIRABLE_ERROR = re.compile(
    r"^(SyntaxError|IndentationError|TabError|NameError|UnboundLocalError|TypeError|ValueError|"
    r"IndexError|KeyError|AttributeError|ZeroDivisionError)(?=:|$)", re.MULTILINE,
)
ERROR_LINE = re.compile(r'File "[^"]*", line (\d+)')
REPAIR_SYSTEM = (
    "修复给出的单文件 Python 程序，保留题意与输入输出。"
    "只返回 [文件名: main.py] 后接一个完整 python Markdown 代码块。"
    "错误类别与行号只用于定位；题目禁止的内置函数或高级写法不得使用，改用 if、循环等基础语句实现。"
    "生成普通标准库控制台程序。"
)


class ApprovedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(max_length=120)
    content: str = Field(max_length=8192)

    @field_validator("filename")
    @classmethod
    def plain_text_filename(cls, name: str) -> str:
        if (not SAFE_FILENAME.fullmatch(name) or WINDOWS_DEVICE.fullmatch(name.split(".", 1)[0].rstrip())
                or Path(name).suffix.lower() not in {".txt", ".csv", ".json"}):
            raise ValueError("请选择一个普通 TXT、CSV 或 JSON 文件。")
        return name

    @field_validator("content")
    @classmethod
    def limit_file_bytes(cls, content: str) -> str:
        if len(content.encode("utf-8")) > 8192:
            raise ValueError("文件最多 8 KiB。")
        return content


class CaseSpec(BaseModel):
    """一组验收用例：输入 + 预期输出。"""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=40)
    stdin: str = Field(default="", max_length=4096)
    expected: str | None = Field(max_length=4096)


def python_input_name(code: str, selected: str) -> str:
    """Stage the approved file under one unambiguous literal input name."""
    if "VISIONSOUL_DATA_FILE" in code:
        return selected
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return selected
    inputs = set()
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "open"
                and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
            continue
        mode = call.args[1] if len(call.args) > 1 else next(
            (kw.value for kw in call.keywords if kw.arg == "mode"), ast.Constant("r"))
        if isinstance(mode, ast.Constant) and mode.value in ("r", "rt", "rb"):
            inputs.add(call.args[0].value)
    if len(inputs) != 1:
        return selected
    try:
        return ApprovedFile.plain_text_filename(next(iter(inputs)))
    except ValueError:
        return selected


class PythonTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 兼容旧版单组样例；有 cases 时以 cases 为准。
    stdin: str = Field(default="", max_length=4096)
    expected: str | None = Field(default=None, max_length=4096)
    approved_file: ApprovedFile | None = None
    cases: list[CaseSpec] = Field(default_factory=list, max_length=MAX_CASES)

    @property
    def has_local_data(self) -> bool:
        """是否带验收数据；带了就不把 stdout/stderr 发给模型。"""
        return bool(self.cases or self.stdin or self.expected is not None or self.approved_file)


class CaseResult(BaseModel):
    name: str
    status: Literal["passed", "mismatch", "failed", "limit", "ran"]
    stdin: str = ""
    expected: str | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    phase: Literal["compile", "run"] | None = None


class PythonCheck(BaseModel):
    status: Literal["ran", "passed", "mismatch", "failed", "limit", "unavailable", "busy", "awaiting_input", "awaiting_file", "file_unused"]
    detail: str
    stdin: str | None = None
    expected: str | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    attempts: int = 1
    repaired_code: str | None = None
    repair_usage: TokenUsage | None = None
    case_results: list[CaseResult] | None = None
    case_source: Literal["model"] | None = None
    data_file: str | None = None
    output_path: str | None = None
    error_line: int | None = None
    source_line: str | None = None
    sample_file: ApprovedFile | None = None
    # C 链路用：区分"编译失败"与"运行失败"。
    phase: Literal["compile", "run"] | None = None


def check_data_file(result: dict, approved_file: ApprovedFile | None, data: dict,
                    *, expected: str | None) -> PythonCheck | None:
    if approved_file:
        if not result.get("file_read") and (result.get("exit_code") == 0 or result.get("missing_file")):
            return PythonCheck(status="file_unused",
                               detail=f"程序没有读取所选文件「{approved_file.filename}」。请检查代码是否使用了本轮数据文件，或要求重新生成完整代码后试跑。",
                               stderr=result["stderr"], exit_code=result["exit_code"], phase=data.get("phase"))
        if result.get("file_read"):
            data["data_file"] = approved_file.filename
        else:
            data["stdout"] = ""
    elif result.get("missing_file") and (expected is None or result.get("exit_code") != 0):
        return PythonCheck(status="awaiting_file", detail="程序需要数据文件。请选择并授权本轮读取的文件后再试。",
                           phase=data.get("phase"))
    return None


def denied_output_detail(result: dict) -> str | None:
    if result.get("denied_write"):
        prefix = "已读取所选数据文件，但" if result.get("file_read") else ""
        return f"{prefix}程序尝试写入文件，导致试跑中断。请让程序将结果打印到桌面窗口后再试。"
    return None


def check_python_before_data(code: str) -> PythonCheck:
    """Compile Python in memory to report syntax errors without executing it."""
    try:
        compile(code, "program.py", "exec", dont_inherit=True)
    except SyntaxError as exc:
        return PythonCheck(status="failed", phase="compile", detail="Python 源码检查未通过。",
                           stderr=f"第 {exc.lineno or 1} 行：{exc.msg}")
    return PythonCheck(status="awaiting_file", phase="compile", attempts=0,
                       detail="代码已通过语法检查。请选择真实数据文件，再点击「授权并试跑」。")


def _run_once(settings: Settings, code: str, stdin: str, expected: str | None,
              approved_file: ApprovedFile | None) -> PythonCheck:
    """跑一次独立沙箱。sandbox 不可用时立即返回，不做任何本机执行。"""
    try:
        runtime = settings.python_sandbox_runtime.resolve()
        if not (runtime / "python.wasm").is_file() or not (runtime / "lib").is_dir():
            return PythonCheck(status="unavailable", detail="本机 Python 沙箱未就绪，本轮未运行。", attempts=0)
    except OSError:
        logger.warning("Python 沙箱运行时不可读", exc_info=True)
        return PythonCheck(status="unavailable", detail="本机 Python 沙箱运行时不可读，请检查权限。", attempts=0)
    if not RUN_LOCK.acquire(blocking=False):
        return PythonCheck(status="busy", detail="已有 Python 沙箱任务运行中，本轮未运行。", attempts=0)
    try:
        root = settings.local_source_workspace
        workspace = source_directory(root.parent.resolve(), root.name)
        jobs = source_directory(workspace, ".sandbox-jobs")
        with TemporaryDirectory(prefix="python-", dir=jobs) as directory:
            job = Path(directory)
            guest = source_directory(job, "guest")
            # 沙箱本轮目录也必须留在 workspace 内。
            ensure_workspace_path(guest, workspace)
            (guest / "program.py").write_text(code, encoding="utf-8")
            (job / "stdin.txt").write_text(stdin, encoding="utf-8", newline="\n")
            guest_filename = None
            if approved_file:
                guest_filename = python_input_name(code, approved_file.filename)
                (guest / guest_filename).write_text(approved_file.content, encoding="utf-8", newline="\n")
            # 子进程执行的是固定的可信 WASI 启动器。用户代码只作为沙箱输入文件。
            allowed_env = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
                           "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"}
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed_env}
            execution = subprocess.run(
                [sys.executable, "-I", "-B", str(WORKER), str(runtime), str(job),
                 *([guest_filename] if guest_filename else [])],
                cwd=job, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15, check=True,
            )
            result = json.loads(execution.stdout)
    except subprocess.TimeoutExpired:
        return PythonCheck(status="limit", detail="沙箱超过总时限，任务已结束。")
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.warning("Python 沙箱启动或清理失败", exc_info=True)
        return PythonCheck(status="unavailable", detail="Python 沙箱无法完成任务，请检查本机配置。")
    finally:
        RUN_LOCK.release()

    if result.get("unavailable"):
        return PythonCheck(status="unavailable", detail="Python 沙箱初始化失败，本轮未运行。", attempts=0)
    data = {"stdout": result["stdout"], "stderr": result["stderr"], "exit_code": result["exit_code"]}
    file_check = check_data_file(result, approved_file, data, expected=expected)
    if result["limit"]:
        details = {
            "output": "输出超过 16 KiB 上限，沙箱已停止。",
            "resource": "达到沙箱时间、计算或内存边界，任务已停止。",
            "trap": "运行被隔离器终止。",
        }
        return PythonCheck(status="limit", detail=details[result["limit"]], **data)
    if file_check is not None:
        return file_check
    if result["exit_code"] != 0:
        last_error = result["stderr"].rstrip().splitlines()[-1:]
        if last_error == ["EOFError: EOF when reading a line"]:
            return PythonCheck(status="awaiting_input", detail="等待输入：请补齐标准输入后再次试跑。", **data)
        detail = denied_output_detail(result)
        if detail is None and re.search(r"^(PermissionError|FileNotFoundError):", result["stderr"], re.MULTILINE):
            detail = "程序无法访问所需文件。若题目确实需要该文件，可在验收样例里授权，并用文件名读取。"
        return PythonCheck(status="failed", detail=detail or "Python 程序退出失败，请查看错误输出。", **data)
    if expected is None:
        return PythonCheck(status="ran", detail="已在沙箱运行。未提供预期输出，未判定题目正确性。", **data)
    if matches_screen_output(result["stdout"], expected):
        return PythonCheck(status="passed", detail="沙箱输出与本轮预期输出一致。", **data)
    return PythonCheck(status="mismatch", detail="程序已运行，输出与预期不一致。", **data)


def run_case_matrix(run_once, test: PythonTest) -> PythonCheck:
    """逐组跑独立沙箱并汇总；run_once(stdin, expected) 负责单次运行。

    Python 与 C 的验收共用这套汇总规则：全部通过才算 passed，任一不符为
    mismatch，运行崩溃为 failed，环境不可用则立即返回。
    """
    if not test.cases:
        result = run_once(test.stdin, test.expected)
        if result.phase != "compile" and result.status not in {"unavailable", "busy"}:
            result.stdin = test.stdin
            result.expected = test.expected
        return result

    results: list[CaseResult] = []
    for index, case in enumerate(test.cases, 1):
        name = case.name.strip() or f"用例{index}"
        one = run_once(case.stdin, case.expected)
        if one.status in {"unavailable", "busy", "awaiting_input", "awaiting_file", "file_unused"}:
            return one.model_copy(update={"case_results": results})
        results.append(CaseResult(name=name, status=one.status, stdin=case.stdin,
                                  expected=case.expected, stdout=one.stdout, stderr=one.stderr,
                                  exit_code=one.exit_code, phase=one.phase))
        if one.status == "limit" or one.phase == "compile":
            return one.model_copy(update={"case_results": results})

    broken = [row for row in results if row.status in {"failed", "limit"}]
    wrong = [row for row in results if row.status == "mismatch"]
    first_bad = (broken or wrong or [None])[0]
    sample = first_bad or results[-1]
    data = {"stdout": sample.stdout, "stderr": sample.stderr, "exit_code": sample.exit_code, "data_file": one.data_file}
    total = len(results)
    if broken:
        return PythonCheck(status="failed", detail=f"{len(broken)}/{total} 组运行失败（首个：{broken[0].name}）。",
                           phase=sample.phase, case_results=results, **data)
    if wrong:
        return PythonCheck(status="mismatch", detail=f"{len(wrong)}/{total} 组输出与预期不一致（首个：{wrong[0].name}）。",
                           case_results=results, **data)
    verified = sum(row.status == "passed" for row in results)
    if verified < total:
        return PythonCheck(status="ran", detail=f"{verified}/{total} 组有预期输出并通过，其他用例仅试跑。",
                           case_results=results, **data)
    return PythonCheck(status="passed", detail=f"{total} 组断言全部通过。", case_results=results, **data)


def run_python_sandbox(settings: Settings, code: str, test: PythonTest) -> PythonCheck:
    """有 cases 时逐组跑独立沙箱并汇总；否则沿用旧版单组样例。"""
    return run_case_matrix(
        lambda stdin, expected: _run_once(settings, code, stdin, expected, test.approved_file), test)


def _repair_payload(task: str, code: str, check: PythonCheck) -> dict:
    matches = REPAIRABLE_ERROR.findall(check.stderr)
    error_type = "OutputMismatch" if check.status == "mismatch" else (matches[-1] if matches else "RuntimeError")
    payload = {"task": task[:2000], "code": code, "status": check.status, "error_type": error_type}
    lines = ERROR_LINE.findall(check.stderr)
    if lines:
        payload["error_lines"] = [int(value) for value in lines[-3:]]
    if check.case_results:
        payload["failed_cases"] = [
            {"index": index, "status": row.status}
            for index, row in enumerate(check.case_results, 1) if row.status != "passed"
        ]
    return payload


def debug_python(settings: Settings, client: DeepSeekClient, task: str, code: str,
                 test: PythonTest) -> PythonCheck:
    """试跑；失败或输出不符时最多自动修复 3 次，每次都在新沙箱复跑。"""
    check = run_python_sandbox(settings, code, test)
    if not settings.python_sandbox_auto_repair:
        return check

    current, usage, repairs = code, None, 0
    while repairs < MAX_REPAIR_ATTEMPTS and check.status in AUTOFIX_STATUSES:
        payload = _repair_payload(task, current, check)
        try:
            reply = client.complete([
                {"role": "system", "content": REPAIR_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ], max_tokens=settings.video_max_tokens)
        except DeepSeekError:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": f"{check.detail} 修复请求失败，已保留当前结果。"})
        usage = add_usage(usage, reply.usage)
        if reply.truncated:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": "修复回复不完整，已保留当前结果。"})
        try:
            name, replacement = parse_generated_code(reply.answer)
            if Path(name).suffix != ".py" or replacement == current:
                raise ValueError("unexpected repair")
        except ValueError:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": "未取得可用的修复代码，已保留当前结果。"})

        current = replacement
        check = run_python_sandbox(settings, current, test)
        repairs += 1
        if check.status in {"ran", "passed"}:
            return check.model_copy(update={"attempts": repairs + 1, "repaired_code": current,
                                            "repair_usage": usage,
                                            "detail": f"{check.detail}（自动修复 {repairs} 次后通过）"})

    if not repairs:
        return check
    detail = f"自动修复 {repairs} 次仍未通过，保留当前结果。{check.detail}"
    return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage, "detail": detail})
