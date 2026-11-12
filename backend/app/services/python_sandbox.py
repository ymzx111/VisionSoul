"""启动一次性 WASI Python 测试，校验输出并清理本轮文件。"""
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
from app.services.deepseek import DeepSeekClient, DeepSeekError, TokenUsage
from app.services.source_files import SAFE_FILENAME, WINDOWS_DEVICE, parse_generated_code, source_directory

logger = logging.getLogger(__name__)
RUN_LOCK = Lock()
WORKER = Path(__file__).with_name("python_wasi_worker.py")
REPAIRABLE_ERROR = re.compile(
    r"^(SyntaxError|IndentationError|TabError|NameError|UnboundLocalError|TypeError|ValueError|"
    r"IndexError|KeyError|AttributeError|ZeroDivisionError)(?=:|$)", re.MULTILINE,
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


class PythonTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stdin: str = Field(default="", max_length=4096)
    expected: str | None = Field(default=None, max_length=4096)
    approved_file: ApprovedFile | None = None


class PythonCheck(BaseModel):
    status: Literal["ran", "passed", "mismatch", "failed", "limit", "unavailable", "busy"]
    detail: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    attempts: int = 1
    repaired_code: str | None = None
    repair_usage: TokenUsage | None = None


def run_python_sandbox(settings: Settings, code: str, test: PythonTest) -> PythonCheck:
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
            (guest / "program.py").write_text(code, encoding="utf-8")
            (job / "stdin.txt").write_text(test.stdin, encoding="utf-8", newline="\n")
            if test.approved_file:
                file = test.approved_file
                (guest / file.filename).write_text(file.content, encoding="utf-8", newline="\n")
            # 子进程执行的是固定的可信 WASI 启动器。用户代码只作为沙箱输入文件。
            allowed_env = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
                           "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"}
            env = {key: value for key, value in os.environ.items() if key.upper() in allowed_env}
            execution = subprocess.run(
                [sys.executable, "-I", "-B", str(WORKER), str(runtime), str(job)],
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
    if result["limit"]:
        details = {
            "output": "输出超过 16 KiB 上限，沙箱已停止。",
            "resource": "达到沙箱时间、计算或内存边界，任务已停止。",
            "trap": "运行被隔离器终止。",
        }
        return PythonCheck(status="limit", detail=details[result["limit"]], **data)
    if result["exit_code"] != 0:
        detail = "Python 程序退出失败，请查看错误输出。"
        if re.search(r"^(PermissionError|FileNotFoundError):", result["stderr"], re.MULTILINE):
            detail = "程序无法访问所需文件。若题目确实需要该文件，可在验收样例里授权，并用文件名读取。"
        return PythonCheck(status="failed", detail=detail, **data)
    if test.expected is None:
        return PythonCheck(status="ran", detail="已在沙箱运行。未提供预期输出，未判定题目正确性。", **data)
    actual = result["stdout"].replace("\r\n", "\n").removesuffix("\n")
    expected = test.expected.replace("\r\n", "\n").removesuffix("\n")
    if actual == expected:
        return PythonCheck(status="passed", detail="沙箱输出与本轮预期输出一致。", **data)
    return PythonCheck(status="mismatch", detail="程序已运行，输出与预期不一致。", **data)


def debug_python(settings: Settings, client: DeepSeekClient, task: str, code: str,
                 test: PythonTest) -> PythonCheck:
    first = run_python_sandbox(settings, code, test)
    # 外部文件、样例输入和输出只用于本机验收。修复只发送原任务、源码和错误类型。
    if (not settings.python_sandbox_auto_repair or first.status != "failed"
            or test.approved_file or test.stdin or test.expected is not None):
        return first
    matches = REPAIRABLE_ERROR.findall(first.stderr)
    if not matches:
        return first
    repair = None
    try:
        repair = client.complete([
            {"role": "system", "content": "修复给出的单文件 Python 程序，保留题意与输入输出。"
                "只返回 [文件名: main.py] 后接一个完整 python Markdown 代码块。"
                "错误类别只用于定位。生成普通标准库控制台程序。"},
            {"role": "user", "content": json.dumps({"task": task[:2000], "code": code, "error_type": matches[-1]},
                                                  ensure_ascii=False)},
        ], max_tokens=settings.video_max_tokens)
        if repair.truncated:
            return first.model_copy(update={"detail": "修复回复不完整，已保留初次运行结果。", "repair_usage": repair.usage})
        name, replacement = parse_generated_code(repair.answer)
        if Path(name).suffix != ".py" or replacement == code:
            raise ValueError("unexpected repair")
    except (DeepSeekError, ValueError):
        return first.model_copy(update={"detail": "未取得可用的修复代码，已保留初次运行结果。",
                                        "repair_usage": repair.usage if repair else None})

    second = run_python_sandbox(settings, replacement, test)
    if second.status == "ran":
        return second.model_copy(update={"attempts": 2, "repaired_code": replacement, "repair_usage": repair.usage,
            "detail": "已修复一次，并在新沙箱试跑成功。未提供预期输出。"})
    return second.model_copy(update={"attempts": 2, "repair_usage": repair.usage,
        "detail": f"修复后的试跑未通过，保留原源码。{second.detail}"})
