"""Compile and run C/C++ using separate WebAssembly sandboxes."""
import base64
from hashlib import file_digest
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock

from app.core.config import Settings
from app.services.deepseek import DeepSeekClient, DeepSeekError, add_usage
from app.services.python_sandbox import MAX_REPAIR_ATTEMPTS, PythonCheck, PythonTest, check_data_file, run_case_matrix
from app.services.source_files import ensure_workspace_path, parse_generated_code, source_directory

logger = logging.getLogger(__name__)
RUN_LOCK = Lock()
WORKER = Path(__file__).with_name("c_wasi_worker.py")
COMPILER_WORKER = Path(__file__).with_name("c_compile_worker.mjs")
COMPILE_TIMEOUT = 45
RUN_TIMEOUT = 15
COMPILER_FILES = {
    "bundle.js": "c7ce70fb627eedc9bd6159928743da85eac7b4729aa2e776ae3771ae76c91dd6",
    "llvm-resources.tar": "79eef0c336fe55cf03ff8f5b42b784c8168f929a3603138b2c6301f4601e4c86",
    "llvm.core.wasm": "24fbed474c7b5b4968fd73fc4827440b93fb351c1b6264516130300eff3e7bf5",
    "llvm.core2.wasm": "960c326eb9b5db7aedbc169540421587a2d3f3ff987e93d6ad5b4da43430ffd4",
    "llvm.core3.wasm": "63680c043192abac4700bbda6a78e4c19b139fa086b1e872d1c6915d37a428a8",
    "llvm.core4.wasm": "f544dc9cc46f88a0f22d1b839a4fb0853dce2d6e231d880bd19754c59a5a234d",
}
LANGUAGES = {
    "c": {"suffixes": {".c"}, "label": "C"},
    "cpp": {"suffixes": {".cpp", ".cc", ".cxx"}, "label": "C++"},
}
LIMIT_DETAIL = {"output": "输出超过 16 KiB 上限，沙箱已停止。",
                "resource": "达到沙箱时间、计算或内存边界，任务已停止。",
                "trap": "运行被隔离器终止。"}
COMPILE_ERROR = re.compile(r"main\.(?:c|cpp):(\d+):(\d+): error: (.+)")
RUNTIME_LINE = re.compile(r"main\.(?:c|cpp):(\d+)")


def _repair_system(language: str) -> str:
    label = LANGUAGES[language]["label"]
    suffix = "main.c" if language == "c" else "main.cpp"
    return (f"修复给出的单文件 {label} 程序（{label} 标准库，目标 wasm32-wasi），保留题意与输入输出。"
            f"只返回 [文件名: {suffix}] 后接一个完整代码块。"
            "编译错误与行号只用于定位；题目禁止的库函数或高级写法不得使用，改用 if、循环等基础语句实现。"
            "不使用网络、文件系统和命令行参数。")


def _compare(actual: str, expected: str) -> bool:
    return (actual.replace("\r\n", "\n").removesuffix("\n")
            == expected.replace("\r\n", "\n").removesuffix("\n"))


def _allowed_env() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
               "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def run_compiler(bundle: Path, language: str, code: str, directory: Path) -> dict:
    node = shutil.which("node")
    if not node:
        raise OSError("Node.js is required for Wasm compilation")
    bundle = bundle.resolve(strict=True)
    if bundle.name != "bundle.js":
        raise OSError("Expected the pinned YoWASP bundle.js")
    assets = []
    for name, digest in COMPILER_FILES.items():
        asset = bundle.parent / name
        if asset.resolve(strict=True) != asset:
            raise OSError("Redirected compiler asset")
        with asset.open("rb") as file:
            if file_digest(file, "sha256").hexdigest() != digest:
                raise OSError("Compiler asset integrity check failed")
        assets.append(f"--allow-fs-read={asset}")
    worker = COMPILER_WORKER.resolve(strict=True)
    compiled = subprocess.run(
        [node, "--permission", f"--allow-fs-read={worker}", *assets,
         "--max-old-space-size=512", str(worker), str(bundle), language],
        cwd=directory, env=_allowed_env(), input=code.encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=COMPILE_TIMEOUT, check=True,
    )
    if len(compiled.stdout) > 12 * 1024 * 1024:
        raise ValueError("Compiler response exceeds size limit")
    response = json.loads(compiled.stdout)
    if not isinstance(response, dict):
        raise ValueError("Invalid compiler response")
    return response


def _run_once(settings: Settings, code: str, language: str, stdin: str, expected: str | None,
              approved_file, cache_root: Path) -> PythonCheck:
    spec = LANGUAGES[language]
    compiler = settings.c_sandbox_compiler
    if not RUN_LOCK.acquire(blocking=False):
        return PythonCheck(status="busy", detail="已有 C/C++ 沙箱任务运行中，本轮未运行。", attempts=0)
    try:
        module = cache_root / "program.wasm"
        root = settings.local_source_workspace
        workspace = source_directory(root.parent.resolve(), root.name)
        jobs = source_directory(workspace, ".sandbox-jobs")
        with TemporaryDirectory(prefix=f"{language}-", dir=jobs) as directory:
            job = Path(directory)
            guest = source_directory(job, "guest")
            ensure_workspace_path(guest, workspace)
            (job / "stdin.txt").write_text(stdin, encoding="utf-8", newline="\n")
            if not module.is_file():
                compiled = run_compiler(compiler, language, code, job)
                if compiled["status"] == "failed":
                    return PythonCheck(status="failed", phase="compile",
                                       detail=f"{spec['label']} 源码编译失败。", stderr=compiled["diagnostics"])
                if compiled["status"] == "limit":
                    return PythonCheck(status="limit", phase="compile", detail="编译产物超过 8 MiB 上限。")
                if compiled["status"] != "ok":
                    raise ValueError("unexpected compiler response")
                artifact = base64.b64decode(compiled["module"], validate=True)
                if len(artifact) > 8 * 1024 * 1024 or not artifact.startswith(b"\x00asm"):
                    raise ValueError("invalid compiler artifact")
                with module.open("xb") as output:
                    output.write(artifact)
            if approved_file:
                (guest / approved_file.filename).write_text(
                    approved_file.content, encoding="utf-8", newline="\n")
            executed = subprocess.run(
                [sys.executable, "-I", "-B", str(WORKER), str(module), str(job),
                 *([approved_file.filename] if approved_file else [])],
                cwd=job, env=_allowed_env(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=RUN_TIMEOUT, check=True,
            )
            result = json.loads(executed.stdout)
    except subprocess.TimeoutExpired:
        return PythonCheck(status="limit", phase="compile" if not module.is_file() else "run", detail="编译或运行超过时限，任务已结束。")
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.warning("C/C++ 沙箱编译或运行失败", exc_info=True)
        return PythonCheck(status="unavailable", detail="C/C++ 隔离环境不可用，请检查 Wasm 编译器和 Node.js。本轮已停止。")
    finally:
        RUN_LOCK.release()

    if result.get("unavailable"):
        return PythonCheck(status="unavailable", detail="C/C++ 沙箱初始化失败，本轮未运行。", attempts=0)
    data = {"stdout": result["stdout"], "stderr": result["stderr"], "exit_code": result["exit_code"],
            "phase": "run"}
    file_check = check_data_file(result, approved_file, data, expected=expected)
    if result["limit"]:
        return PythonCheck(status="limit", detail=LIMIT_DETAIL[result["limit"]], **data)
    if file_check is not None:
        return file_check
    if result.get("awaiting_input"):
        return PythonCheck(status="awaiting_input", detail="程序需要标准输入。请按题目要求补充输入后试跑。", **data)
    if result["exit_code"] != 0:
        return PythonCheck(status="failed", detail=f"{spec['label']} 程序退出失败，请查看错误输出。", **data)
    if expected is None:
        return PythonCheck(status="ran", detail="已在沙箱编译并运行。未提供预期输出，未判定题目正确性。", **data)
    if _compare(result["stdout"], expected):
        return PythonCheck(status="passed", detail="沙箱输出与本轮预期输出一致。", **data)
    return PythonCheck(status="mismatch", detail="程序已运行，输出与预期不一致。", **data)


def run_c_sandbox(settings: Settings, code: str, language: str, test: PythonTest) -> PythonCheck:
    """同一轮同一份源码只编译一次，每组用例使用同一份临时 Wasm。"""
    if language not in LANGUAGES:
        raise ValueError("只支持 C 与 C++。")
    compiler = settings.c_sandbox_compiler
    if not compiler.is_file():
        return PythonCheck(status="unavailable", detail="本机 C/C++ 编译工具未安装，本轮已停止。请运行 tools/install_c_sandbox.py。", attempts=0)
    root = settings.local_source_workspace
    workspace = source_directory(root.parent.resolve(), root.name)
    jobs = source_directory(workspace, ".sandbox-jobs")
    with TemporaryDirectory(prefix="c-cache-", dir=jobs) as cache:
        return run_case_matrix(
            lambda stdin, expected: _run_once(settings, code, language, stdin, expected,
                                              test.approved_file, Path(cache)), test)


def _repair_payload(task: str, code: str, check: PythonCheck) -> dict:
    """仅发送失败类型、行号和用例序号。"""
    payload = {"task": task[:2000], "code": code, "status": check.status, "phase": check.phase or "run"}
    if check.phase == "compile":
        payload["error_type"] = "CompileError"
        payload["error_lines"] = [int(match.group(1)) for match in COMPILE_ERROR.finditer(check.stderr)][:10]
        return payload
    payload["error_type"] = "OutputMismatch" if check.status == "mismatch" else "RuntimeError"
    lines = RUNTIME_LINE.findall(check.stderr)
    if lines:
        payload["error_lines"] = sorted({int(value) for value in lines})[-5:]
    if check.status == "mismatch":
        payload["hint"] = "程序可以运行，但输出与题目预期不一致，请检查计算逻辑或输出格式。"
    if check.case_results:
        payload["failed_cases"] = [{"index": index, "status": row.status}
                                   for index, row in enumerate(check.case_results, 1) if row.status != "passed"]
    return payload


def debug_c(settings: Settings, client: DeepSeekClient, task: str, code: str, language: str,
            test: PythonTest) -> PythonCheck:
    """编译或运行失败、输出不符时最多自动修复 3 次，每次都在新沙箱重编重跑。"""
    check = run_c_sandbox(settings, code, language, test)
    if not settings.c_sandbox_auto_repair:
        return check

    spec = LANGUAGES[language]
    current, usage, repairs = code, None, 0
    while repairs < MAX_REPAIR_ATTEMPTS and check.status in {"failed", "mismatch"}:
        payload = _repair_payload(task, current, check)
        try:
            reply = client.complete([
                {"role": "system", "content": _repair_system(language)},
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
            if Path(name).suffix.lower() not in spec["suffixes"] or replacement == current:
                raise ValueError("unexpected repair")
        except ValueError:
            return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage,
                                            "detail": "未取得可用的修复代码，已保留当前结果。"})

        current = replacement
        check = run_c_sandbox(settings, current, language, test)
        repairs += 1
        if check.status in {"ran", "passed"}:
            return check.model_copy(update={"attempts": repairs + 1, "repaired_code": current,
                                            "repair_usage": usage,
                                            "detail": f"{check.detail}（自动修复 {repairs} 次后通过）"})

    if not repairs:
        return check
    detail = f"自动修复 {repairs} 次仍未通过，保留当前结果。{check.detail}"
    return check.model_copy(update={"attempts": repairs + 1, "repair_usage": usage, "detail": detail})
