"""编译并运行单文件 C 程序：编译在宿主机受限执行，运行只发生在 WASI 沙箱内。

安全边界：
- 编译器是固定路径的 Zig，参数固定，不接受用户额外开关；
- 编译工作目录锁在 workspace/.sandbox-jobs 的临时目录内；
- 产物 wasm 交给 c_wasi_worker，在 Wasmtime 里跑，无网络、无子进程、限量限时；
- 沙箱或编译器不可用时直接停止，不退回到本机直接执行。
"""
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock

from app.core.config import Settings
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.python_sandbox import MAX_REPAIR_ATTEMPTS, PythonCheck, PythonTest, run_case_matrix
from app.services.source_files import ensure_workspace_path, parse_generated_code, source_directory

logger = logging.getLogger(__name__)
RUN_LOCK = Lock()
WORKER = Path(__file__).with_name("c_wasi_worker.py")
COMPILE_TIMEOUT = 30
RUN_TIMEOUT = 15
OUTPUT_LIMIT_DETAIL = {"output": "输出超过 16 KiB 上限，沙箱已停止。",
                       "resource": "达到沙箱时间、计算或内存边界，任务已停止。",
                       "trap": "运行被隔离器终止。"}
COMPILE_ERROR = re.compile(r"main\.c:(\d+):(\d+): error: (.+)")
RUNTIME_LINE = re.compile(r"main\.c:(\d+)")
REPAIR_SYSTEM = (
    "修复给出的单文件 C 程序（C11 标准库，目标 wasm32-wasip1），保留题意与输入输出。"
    "只返回 [文件名: main.c] 后接一个完整 c Markdown 代码块。"
    "编译错误与行号只用于定位；题目禁止的库函数或高级写法不得使用，改用 if、循环等基础语句实现。"
    "不使用网络、文件系统和命令行参数。"
)


def _compare(actual: str, expected: str) -> bool:
    return (actual.replace("\r\n", "\n").removesuffix("\n")
            == expected.replace("\r\n", "\n").removesuffix("\n"))


def _allowed_env() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
               "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _run_once(settings: Settings, code: str, stdin: str, expected: str | None,
              approved_file) -> PythonCheck:
    compiler = settings.c_sandbox_compiler
    if not compiler.is_file():
        return PythonCheck(status="unavailable", detail="本机 C 沙箱编译器未安装，本轮未编译。", attempts=0)
    if not RUN_LOCK.acquire(blocking=False):
        return PythonCheck(status="busy", detail="已有 C 沙箱任务运行中，本轮未运行。", attempts=0)
    try:
        root = settings.local_source_workspace
        workspace = source_directory(root.parent.resolve(), root.name)
        jobs = source_directory(workspace, ".sandbox-jobs")
        with TemporaryDirectory(prefix="c-", dir=jobs) as directory:
            job = Path(directory)
            guest = source_directory(job, "guest")
            ensure_workspace_path(guest, workspace)
            (guest / "main.c").write_text(code, encoding="utf-8", newline="\n")
            (job / "stdin.txt").write_text(stdin, encoding="utf-8", newline="\n")
            if approved_file:
                (guest / approved_file.filename).write_text(
                    approved_file.content, encoding="utf-8", newline="\n")
            module = job / "program.wasm"
            # Zig 用环境变量定位缓存；指到本轮临时目录，避免写入用户目录。
            compile_env = _allowed_env()
            compile_env["ZIG_LOCAL_CACHE_DIR"] = str(job / "zig-cache")
            compile_env["ZIG_GLOBAL_CACHE_DIR"] = str(job / "zig-global-cache")
            compiled = subprocess.run(
                [str(compiler), "cc", "-target", "wasm32-wasi", "-O1", "-o", str(module), "main.c"],
                cwd=guest, env=compile_env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=COMPILE_TIMEOUT, check=False,
            )
            if compiled.returncode != 0:
                return PythonCheck(status="failed", phase="compile",
                                   detail="C 程序编译失败，已保留编译器错误。",
                                   stderr=compiled.stderr.decode("utf-8", "replace")[-4096:])
            executed = subprocess.run(
                [sys.executable, "-I", "-B", str(WORKER), str(module), str(job)],
                cwd=job, env=_allowed_env(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=RUN_TIMEOUT, check=True,
            )
            result = json.loads(executed.stdout)
    except subprocess.TimeoutExpired:
        return PythonCheck(status="limit", phase="run", detail="编译或运行超过时限，任务已结束。")
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.warning("C 沙箱编译或运行失败", exc_info=True)
        return PythonCheck(status="unavailable", detail="C 沙箱无法完成任务，请检查本机配置。")
    finally:
        RUN_LOCK.release()

    if result.get("unavailable"):
        return PythonCheck(status="unavailable", detail="C 沙箱初始化失败，本轮未运行。", attempts=0)
    data = {"stdout": result["stdout"], "stderr": result["stderr"], "exit_code": result["exit_code"],
            "phase": "run"}
    if result["limit"]:
        return PythonCheck(status="limit", detail=OUTPUT_LIMIT_DETAIL[result["limit"]], **data)
    if result["exit_code"] != 0:
        return PythonCheck(status="failed", detail="C 程序退出失败，请查看错误输出。", **data)
    if expected is None:
        return PythonCheck(status="ran", detail="已在沙箱编译并运行。未提供预期输出，未判定题目正确性。", **data)
    if _compare(result["stdout"], expected):
        return PythonCheck(status="passed", detail="沙箱输出与本轮预期输出一致。", **data)
    return PythonCheck(status="mismatch", detail="程序已运行，输出与预期不一致。", **data)


def run_c_sandbox(settings: Settings, code: str, test: PythonTest) -> PythonCheck:
    return run_case_matrix(
        lambda stdin, expected: _run_once(settings, code, stdin, expected, test.approved_file), test)


def _repair_payload(task: str, code: str, check: PythonCheck, local_data: bool) -> dict:
    """编译错误整段回传（不含样例数据）；运行失败在带样例时只回传行号与用例名。"""
    payload = {"task": task[:2000], "code": code, "status": check.status, "phase": check.phase or "run"}
    if check.phase == "compile":
        errors = [f"{m.group(1)}:{m.group(2)}: {m.group(3)}" for m in COMPILE_ERROR.finditer(check.stderr)]
        payload["compile_errors"] = errors[:10]
        if not errors:
            payload["compiler"] = check.stderr[-800:]
        return payload
    if local_data:
        payload["error_type"] = "OutputMismatch" if check.status == "mismatch" else "RuntimeError"
        lines = RUNTIME_LINE.findall(check.stderr)
        if lines:
            payload["error_lines"] = sorted({int(value) for value in lines})[-5:]
        if check.status == "mismatch":
            payload["hint"] = "程序可以运行，但输出与题目预期不一致，请检查计算逻辑或输出格式。"
        if check.case_results:
            payload["failed_cases"] = [{"name": row.name, "status": row.status}
                                       for row in check.case_results if row.status != "passed"]
    else:
        payload["stdout"] = check.stdout[-2000:]
        payload["stderr"] = check.stderr[-2000:]
        payload["exit_code"] = check.exit_code
    return payload


def debug_c(settings: Settings, client: DeepSeekClient, task: str, code: str,
            test: PythonTest) -> PythonCheck:
    """编译或运行失败、输出不符时最多自动修复 3 次，每次都在新沙箱重编重跑。"""
    check = run_c_sandbox(settings, code, test)
    if not settings.c_sandbox_auto_repair:
        return check

    current, usage, repairs = code, None, 0
    while repairs < MAX_REPAIR_ATTEMPTS and check.status in {"failed", "mismatch"}:
        payload = _repair_payload(task, current, check, test.has_local_data)
        try:
            reply = client.complete([
                {"role": "system", "content": REPAIR_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ], max_tokens=settings.video_max_tokens)
        except DeepSeekError:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": f"{check.detail} 修复请求失败，已保留当前结果。"})
        usage = reply.usage
        if reply.truncated:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": "修复回复不完整，已保留当前结果。"})
        try:
            name, replacement = parse_generated_code(reply.answer)
            if Path(name).suffix != ".c" or replacement == current:
                raise ValueError("unexpected repair")
        except ValueError:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": "未取得可用的修复代码，已保留当前结果。"})

        current = replacement
        check = run_c_sandbox(settings, current, test)
        repairs += 1
        if check.status in {"ran", "passed"}:
            return check.model_copy(update={"attempts": repairs + 1, "repaired_code": current,
                                            "repair_usage": usage,
                                            "detail": f"{check.detail}（自动修复 {repairs} 次后通过）"})

    if not repairs:
        return check
    detail = f"自动修复 {repairs} 次仍未通过，保留当前结果。{check.detail}"
    return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage, "detail": detail})
