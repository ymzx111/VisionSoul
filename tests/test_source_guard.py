"""源码写入必须留在 workspace 内；越界一律拦截且不落盘。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.source_files import (
    UnsafeWorkspacePath, UnsupportedDeliveryLanguage, ensure_workspace_path, parse_generated_code,
    parse_generated_code_or_infer, save_dated_source,
)


class WorkspacePathGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="visionsoul-guard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def test_normal_source_is_saved_inside_workspace(self):
        target = save_dated_source(self.workspace, "main.py", "print(1)\n")
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, self.workspace / "Python")
        c_source = save_dated_source(self.workspace, "main.c", "int main(void){return 0;}\n")
        self.assertEqual(c_source.parent, self.workspace / "C")

    def test_traversal_and_absolute_names_are_blocked(self):
        for filename in ("../evil.py", r"..\..\evil.py", "a/../../evil.py",
                         str(self.root / "outside.py"), "C:\\Windows\\evil.py"):
            with self.subTest(filename=filename), self.assertRaises(UnsafeWorkspacePath):
                save_dated_source(self.workspace, filename, "print(1)\n")
        self.assertEqual(list(self.workspace.rglob("*.py")), [])

    def test_ensure_rejects_paths_outside_workspace(self):
        with self.assertRaises(UnsafeWorkspacePath):
            ensure_workspace_path(self.root / "workspace2" / "main.py", self.workspace)
        with self.assertRaises(UnsafeWorkspacePath):
            ensure_workspace_path(self.workspace / ".." / "main.py", self.workspace)
        self.assertEqual(ensure_workspace_path(self.workspace / "Python" / "main.py", self.workspace),
                         self.workspace / "Python" / "main.py")

    def test_only_python_and_c_are_accepted(self):
        fence = chr(96) * 3
        for filename, language in (("main.py", "python"), ("main.c", "c")):
            text = f"[文件名: {filename}]\n{fence}{language}\nint main(void){{return 0;}}\n{fence}"
            with self.subTest(filename=filename):
                self.assertEqual(parse_generated_code(text)[0], filename)
        for filename, language in (("main.cs", "csharp"), ("main.cs", "c#"), ("main.java", "java")):
            text = f"[文件名: {filename}]\n{fence}{language}\nclass A {{}}\n{fence}"
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                parse_generated_code(text)

    def test_cpp_cannot_bypass_delivery_through_inference(self):
        fence = chr(96) * 3
        for declaration, language in (("[文件名: main.cpp]\n", "cpp"),
                                      ("[文件名: main.cc]\n", "cc"),
                                      ("[文件名: main.cxx]\n", "cxx"),
                                      ("[语言: C++]\n[文件名: main.c]\n", "c"),
                                      ("[文件名: main.c]\n", "c++"), ("", "cpp")):
            with self.subTest(declaration=declaration, language=language):
                text = f"{declaration}{fence}{language}\nint main() {{ return 0; }}\n{fence}"
                with self.assertRaises(UnsupportedDeliveryLanguage):
                    parse_generated_code_or_infer(text)

    def test_missing_filename_is_inferred_from_single_code_block(self):
        fence = chr(96) * 3
        self.assertEqual(parse_generated_code_or_infer(f"完整源码如下：\n{fence}python\nprint(1)\n{fence}\n"),
                         ("main.py", "print(1)\n"))
        self.assertEqual(parse_generated_code_or_infer(f"{fence}c\nint main(void){{return 0;}}\n{fence}\n")[0],
                         "main.c")
        with self.assertRaises(ValueError):
            parse_generated_code_or_infer(f"{fence}java\nclass A {{}}\n{fence}\n")
        with self.assertRaises(ValueError):
            parse_generated_code_or_infer(
                f"{fence}c\nint main(void){{return 0;}}\n{fence}\n{fence}python\nprint(1)\n{fence}\n")


if __name__ == "__main__":
    unittest.main()
