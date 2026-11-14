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
from uuid import NAMESPACE_URL, uuid5
from xml.sax.saxutils import escape

from app.core.config import Settings
from app.services.python_sandbox import PythonCheck, PythonTest
from app.services.source_files import (
    CODE_REQUEST, FENCE_BLOCK, FILE_DECLARATION, parse_generated_code, parse_generated_code_or_infer,
    save_dated_source, source_directory, write_source,
)

logger = logging.getLogger(__name__)
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
    solution = folder / "VisionSoulCode.sln"
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
        if not isinstance(data, list) or not 1 <= len(data) <= 3:
            return source_reply, PythonTest()
        test = PythonTest.model_validate({"cases": data})
    except ValueError:
        return source_reply, PythonTest()
    unique = {case.stdin for case in test.cases}
    if len(unique) != len(test.cases):
        return source_reply, PythonTest()
    return source_reply, test


def open_generated_source(settings: Settings, turn: dict,
                          verify_python: Callable[[str], PythonCheck] | None = None,
                          verify_c: Callable[[str, str], PythonCheck] | None = None) -> dict | None:
    reply = turn["assistant_message"]["content"]
    requested = bool(CODE_REQUEST.search(((turn.get("user_message") or {}).get("content") or "")))
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
            "回复“请给出完整源码”可以重试。")}
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
                "detail": f"{exc} 本轮未保存也未打开编辑器，回复“请给出完整源码”可以重试。"}
    except OSError:
        logger.warning("本机源码写入失败", exc_info=True)
        return {"status": "error", "detail": "源码写入失败，请检查 workspace 写入权限。"}

    check = None
    if extension == ".py" and verify_python:
        check = verify_python(code)
    elif extension in {".c", ".cpp", ".cc", ".cxx"} and verify_c:
        # C/C++ 由宿主机编译成 wasm，再在 WASI 沙箱里运行；结果仍放在 python_check 字段里兼容旧前端。
        check = verify_c(code, "c" if extension == ".c" else "cpp")
    if check and check.repaired_code:
        try:
            target = save_dated_source(workspace, filename, check.repaired_code)
        except (OSError, ValueError):
            logger.warning("修复版源码保存失败", exc_info=True)
            check = check.model_copy(update={"detail": "修复版在沙箱试跑成功，但写入失败。当前打开的是原始源码。"})
    result = {"path": str(target), "ide": ide, "python_check": check}
    if (not launcher or not launcher.is_absolute() or launcher.suffix.lower() != ".exe" or not launcher.is_file()
            or (ide == "Python IDLE" and not (find_spec("idlelib") and find_spec("tkinter")))):
        return {**result, "status": "saved", "detail": f"已保存源码，未找到已配置的 {ide}。"}
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
        return {**result, "status": "saved", "detail": f"已保存源码，{ide} 打开失败，可手动打开文件。"}
    return {**result, "status": "opened", "detail": f"已发送到 {ide}。"}
