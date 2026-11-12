"""源码写入必须留在 workspace 内；越界一律拦截且不落盘。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.source_files import (
    UnsafeWorkspacePath, ensure_workspace_path, save_dated_source,
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


if __name__ == "__main__":
    unittest.main()
