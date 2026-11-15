"""将已保存的单份图片或视频解题回复交给本机 IDE。"""
import ast
import json
from collections.abc import Callable
from importlib.util import find_spec
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Thread
from uuid import NAMESPACE_URL, uuid5
from xml.sax.saxutils import escape

from app.core.config import Settings
from app.services.python_sandbox import PythonCheck, PythonTest
from app.services.code_reruns import remember_source
from app.services.source_files import (
    CODE_REQUEST, FENCE_BLOCK, FILE_DECLARATION, parse_generated_code, parse_generated_code_or_infer,
    save_dated_source, source_directory, write_source,
)

logger = logging.getLogger(__name__)
REQUIRES_DATA_FILE = re.compile(r"^\s*\[需要数据文件\]\s*$", re.MULTILINE)
CLARIFICATION_REQUIRED = re.compile(r"^[ \t]*\[需确认题意\][ \t]*$", re.MULTILINE)


def needs_task_clarification(reply: str) -> bool:
    prose = FENCE_BLOCK.sub("", reply.replace("\r\n", "\n"))
    return CLARIFICATION_REQUIRED.search(prose) is not None


CODE_DELIVERY_TASK = (Path(__file__).resolve().parents[1] / "prompts" / "code_delivery.md").read_text(encoding="utf-8").strip()


def visual_studio_solution(source: Path, folder: Path) -> Path:
    """为单文件 C/C++ 源码创建可运行的 Win32 控制台项目。"""
    project = folder / "VisionSoulCode.vcxproj"
    guid = "{" + str(uuid5(NAMESPACE_URL, str(folder))).upper() + "}"
    write_source(project, f'''<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup Label="ProjectConfigurations">
    <ProjectConfiguration Include="Debug|Win32">
      <Configuration>Debug</Configuration><Platform>Win32</Platform>
    </ProjectConfiguration>
  </ItemGroup>
  <PropertyGroup Label="Globals"><ProjectGuid>{guid}</ProjectGuid><Keyword>Win32Proj</Keyword></PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />
  <PropertyGroup Label="Configuration">
    <ConfigurationType>Application</ConfigurationType><UseDebugLibraries>true</UseDebugLibraries>
  </PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.props" />
  <ItemDefinitionGroup><Link><SubSystem>Console</SubSystem></Link></ItemDefinitionGroup>
  <ItemGroup><ClCompile Include="{escape(str(source))}" /></ItemGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />
</Project>
''')
    solution = folder / f"{source.name}.sln"
    write_source(solution, f'''Microsoft Visual Studio Solution File, Format Version 11.00
# Visual Studio 2010
Project("{{BC8A1FFA-BEE3-4634-8014-F334798102B3}}") = "VisionSoulCode", "{project.name}", "{guid}"
EndProject
Global
    GlobalSection(SolutionConfigurationPlatforms) = preSolution
        Debug|Win32 = Debug|Win32
    EndGlobalSection
    GlobalSection(ProjectConfigurationPlatforms) = postSolution
        {guid}.Debug|Win32.ActiveCfg = Debug|Win32
        {guid}.Debug|Win32.Build.0 = Debug|Win32
    EndGlobalSection
EndGlobal
''')
    return solution


def launch_ide(args: list[str], folder: Path) -> None:
    """用 Windows ShellExecute 打开 IDE 的正常窗口，单独传递路径参数。"""
    if os.name != "nt":
        raise OSError("本机 IDE 自动打开仅支持 Windows。")
    env = {key: value for key, value in os.environ.items() if key.upper() != "DEEPSEEK_API_KEY"}
    launch = 'import os, sys; os.startfile(sys.argv[1], "open", sys.argv[2], sys.argv[3], 1)'
    subprocess.run(
        [sys.executable, "-c", launch, args[0], subprocess.list2cmdline(args[1:]), str(folder)],
        cwd=folder, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10,
    )


def show_sandbox_result(source: Path, check: PythonCheck, *, run_id: str | None = None,
                        origin: str | None = None) -> bool:
    """Send rendered data over stdin to the trusted desktop viewer."""
    launcher = Path(sys.executable).with_name("pythonw.exe")
    if os.name != "nt" or not launcher.is_file() or not find_spec("tkinter"):
        return False
    fields = {"detail", "status", "stdin", "stdout", "stderr", "phase", "case_source", "data_file", "output_path",
              "expected", "error_line", "source_line", "sample_file"}
    result = check.model_dump(include=fields)
    result["case_results"] = [row.model_dump(include={"name", "status", "stdin", "stdout", "stderr", "phase", "expected"})
                              for row in check.case_results or []]
    label = {".py": "Python", ".c": "C"}.get(source.suffix.lower(), "C++")
    result.update(name=source.name, editor_path=str(source), language=label)
    if origin and run_id:
        result.update(origin=origin, run_id=run_id)
    script = Path(__file__).with_name("python_result_window.py")
    env = {key: value for key, value in os.environ.items() if key.upper() != "DEEPSEEK_API_KEY"}
    try:
        with TemporaryFile(mode="w+b") as payload:
            payload.write(json.dumps(result).encode("ascii"))
            payload.seek(0)
            viewer = subprocess.Popen([str(launcher), "-I", "-B", str(script)], stdin=payload,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=script.parent, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        Thread(target=viewer.wait, daemon=True).start()
    except (OSError, subprocess.SubprocessError):
        logger.warning("桌面结果窗口启动失败", exc_info=True)
        return False
    return True


def split_model_cases(reply: str) -> tuple[str, PythonTest]:
    # 只接受源码围栏之后独立出现的约定样例块。
    blocks = list(FENCE_BLOCK.finditer(reply.replace("\r\n", "\n")))
    if len(blocks) != 2 or blocks[1]["language"].lower() != "visionsoul-cases":
        return reply, PythonTest()
    block = blocks[1]
    normalized = reply.replace("\r\n", "\n")
    source_reply = normalized[:block.start()] + normalized[block.end():]
    try:
        data = json.loads(block["code"])
        if isinstance(data, dict) and set(data) <= {"sample_file", "stdin", "expected"} and "sample_file" in data:
            sample = PythonTest.model_validate({
                "approved_file": data["sample_file"], "stdin": data.get("stdin", ""), "expected": data.get("expected"),
            })
            if sample.approved_file and sample.approved_file.content.strip():
                return source_reply, sample
        if not isinstance(data, list) or not 1 <= len(data) <= 3:
            return source_reply, PythonTest()
        test = PythonTest.model_validate({"cases": data})
    except ValueError:
        return source_reply, PythonTest()
    # A prose disclaimer supplied by the model is not a stdout assertion.
    cases = [case for case in test.cases if not re.fullmatch(
        r"(?:输出(?:内容)?(?:随|因).{1,50}(?:变化|不同)[，,：:]?\s*)?"
        r"(?:无法|不能|无法预先)(?:给出|确定|预测)(?:固定的?|确切的?)?(?:预期(?:输出)?|输出)[。！!]?",
        (case.expected or "").strip(),
    )]
    test = test.model_copy(update={"cases": cases})
    unique = {case.stdin for case in test.cases}
    if len(unique) != len(test.cases):
        return source_reply, PythonTest()
    return source_reply, test


def incomplete_code_reply(reply: str, caption: str = "") -> bool:
    """Detect delivery promises that did not include a usable source block."""
    if re.search(r"(?:只|仅)(?:需|要)?(?:讲解|分析|概括)|(?:不要|不用|无需|不需要)(?:生成|提供|编写|写)?(?:代码|源码|程序)", caption):
        return False
    prose = FENCE_BLOCK.sub("", reply)
    if needs_task_clarification(reply) or re.search(
            r"请(?:你|先)?(?:补充|确认|提供|重新拍摄)|无法(?:确定|辨认)|题面.{0,8}(?:缺少|看不清)|[？?]", prose):
        return False
    committed = (FILE_DECLARATION.search(reply)
                 or re.search(r"(?:以下|下面|接下来|现)[^。\n]{0,16}(?:源码|完整代码|完整程序)"
                              r"|(?:题目|题面)要求[^。\n]{0,28}(?:编写|实现|写出)[^。\n]{0,24}(?:程序|代码|源码)", reply)
                 or re.search(r"(?:编写|生成|写出|补全)[^。\n]{0,16}(?:代码|源码|程序)", caption))
    if not committed:
        return False
    source_reply, _ = split_model_cases(reply)
    try:
        parser = parse_generated_code if FILE_DECLARATION.search(source_reply) else parse_generated_code_or_infer
        parser(source_reply)
    except ValueError:
        return True
    return False


def reads_standard_input(code: str, suffix: str) -> bool:
    if suffix == ".py":
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input":
                return True
            if isinstance(node, ast.Attribute) and node.attr == "stdin" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                return True
        return False
    return bool(re.search(r"\b(?:scanf|getchar|cin|stdin)\b", code))


def missing_input_sample(reply: str) -> bool:
    if needs_task_clarification(reply):
        return False
    source_reply, test = split_model_cases(reply)
    try:
        filename, code = parse_generated_code_or_infer(source_reply)
    except ValueError:
        return False
    if REQUIRES_DATA_FILE.search(source_reply) or "VISIONSOUL_DATA_FILE" in code:
        return not test.approved_file
    return reads_standard_input(code, Path(filename).suffix) and not any(case.stdin.strip() for case in test.cases)


INPUT_SAMPLE_RETRY = (
    "程序需要输入，但本轮交付缺少有效的非空自测输入。请重新交付同题完整源码，并在源码之后提供 "
    "visionsoul-cases。标准输入题在 JSON 数组中至少给一组能走到主要计算分支的非空 stdin，"
    "文件题给合规的 sample_file。自行按题目规则推导 expected，不使用真实用户数据，不向用户索要样例。"
)


def has_source_and_sample(reply: str) -> bool:
    source_reply, test = split_model_cases(reply)
    if needs_task_clarification(reply) or not (test.approved_file or test.cases) or missing_input_sample(reply):
        return False
    try:
        parse_generated_code_or_infer(source_reply)
    except ValueError:
        return False
    return True


def open_generated_source(settings: Settings, turn: dict,
                          verify_python: Callable[[str], PythonCheck] | None = None,
                          verify_c: Callable[[str, str], PythonCheck] | None = None, *,
                          origin: str | None = None, requires_file: bool = False,
                          output_name: str | None = None) -> dict | None:
    reply = turn["assistant_message"]["content"]
    if needs_task_clarification(reply):
        return None
    caption = (turn.get("user_message") or {}).get("content", "")
    requested = bool(CODE_REQUEST.search(caption) or incomplete_code_reply(reply, caption))
    declared = bool(FILE_DECLARATION.search(reply))
    if declared:
        parser, required = parse_generated_code, True
    elif FENCE_BLOCK.search(reply):
        # 只有代码块没有文件名：按语言推断；用户没要代码时解析失败就安静跳过。
        parser, required = parse_generated_code_or_infer, requested
    else:
        if not requested:
            return None
        return {"status": "error", "detail": (
            "本次回复没有给出完整源码（需要 [文件名: main.py] 加一个完整代码块），未保存也未打开编辑器；"
            "可回复“按题目生成并试跑程序”重试。")}
    try:
        source_reply, _ = split_model_cases(reply)
        filename, code = parser(source_reply)
        extension = Path(filename).suffix
        if extension == ".py":
            if verify_python is None:
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    raise ValueError("Python 代码语法不完整，未生成源文件。") from exc
            launcher = settings.local_pycharm or Path(sys.executable).with_name("pythonw.exe")
            ide = "PyCharm" if settings.local_pycharm else "Python IDLE"
        elif extension in {".c", ".cpp", ".cc", ".cxx"}:
            if not re.search(r"\bmain\s*\([^)]*\)\s*\{", code):
                raise ValueError("C/C++ 代码缺少完整的 main 函数，未生成源文件。")
            launcher, ide = settings.local_visual_studio, "Visual Studio"
        else:
            raise ValueError("自动打开支持 C、C++ 和 Python。")
        root = settings.local_source_workspace
        workspace = source_directory(root.parent.resolve(), root.name)
        target = save_dated_source(workspace, filename, code,
                                   encoding="utf-8-sig" if ide == "Visual Studio" else "utf-8")
    except ValueError as exc:
        if not required:
            return None
        # 明确告诉用户为什么没有跳到编辑器，并给出可以直接重试的说法。
        return {"status": "error",
                "detail": f"{exc} 本轮未保存也未打开编辑器，可回复“按题目生成并试跑程序”重试。"}
    except OSError:
        logger.warning("本机源码写入失败", exc_info=True)
        return {"status": "error", "detail": "源码写入失败，请检查 workspace 写入权限。"}

    language = "python" if extension == ".py" else "c" if extension == ".c" else "cpp"
    check = None
    if extension == ".py" and verify_python:
        check = verify_python(code)
    elif extension in {".c", ".cpp", ".cc", ".cxx"} and verify_c:
        # C/C++ 在 Wasm 编译器中编译，再在独立的 WASI 沙箱运行；结果仍放在 python_check 字段里兼容旧前端。
        check = verify_c(code, language)
    if check and check.repaired_code:
        try:
            target = save_dated_source(workspace, filename, check.repaired_code)
            code = check.repaired_code
        except (OSError, ValueError):
            logger.warning("修复版源码保存失败", exc_info=True)
            check = check.model_copy(update={"detail": "修复版在沙箱试跑成功，但写入失败。当前打开的是原始源码。"})
    result = {"path": str(target), "ide": ide, "python_check": check}
    if check is not None:
        run_id = remember_source(code, target, language, requires_file or check.status == 'awaiting_file', output_name)
        result["sandbox_run_id"] = run_id
        if language == "python":
            result["python_run_id"] = run_id

    def finish(status: str, detail: str) -> dict:
        if check is not None:
            result["desktop_result_opened"] = show_sandbox_result(target, check, run_id=run_id, origin=origin)
        return {**result, "status": status, "detail": detail}

    if (not launcher or not launcher.is_absolute() or launcher.suffix.lower() != ".exe" or not launcher.is_file()
            or (ide == "Python IDLE" and not (find_spec("idlelib") and find_spec("tkinter")))):
        return finish("saved", f"已保存源码，未找到已配置的 {ide}。")
    try:
        if ide == "Visual Studio":
            projects = source_directory(settings.chat_database_path.parent.resolve(), "ide_projects")
            folder = source_directory(projects, f'{turn["conversation_id"]}-{turn["assistant_message"]["id"]}')
            entry = visual_studio_solution(target, folder)
            file_argument = f'"{target}"' if any(char.isspace() for char in str(target)) else str(target)
            args = [str(launcher), str(entry), "/Command", f"File.OpenFile {file_argument}"]
        elif ide == "Python IDLE":
            args = [str(launcher), "-m", "idlelib", "-e", str(target)]
        else:
            args = [str(launcher), str(target)]
        launch_ide(args, target.parent)
    except (OSError, ValueError, subprocess.SubprocessError):
        logger.warning("本机 IDE 启动失败", exc_info=True)
        return finish("saved", f"已保存源码，{ide} 打开失败，可手动打开文件。")
    return finish("opened", f"已发送到 {ide}。")
