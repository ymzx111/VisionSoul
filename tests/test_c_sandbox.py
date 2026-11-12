"""C 隔离编译验收：宿主机编译成 wasm，WASI 沙箱运行，多组断言与三次修复。"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings
from app.services.c_sandbox import debug_c, run_c_sandbox
from app.services.deepseek import ModelReply
from app.services.python_sandbox import CaseSpec, PythonCheck, PythonTest

GOOD_CODE = """#include <stdio.h>
int main(void)
{
    char ch;
    if (scanf("%c", &ch) != 1) {
        return 0;
    }
    if (ch >= 'A' && ch <= 'Z') {
        ch = (char)(ch + ('a' - 'A'));
    } else if (ch >= 'a' && ch <= 'z') {
        ch = (char)(ch - ('a' - 'A'));
    }
    printf("%c\\n", ch);
    return 0;
}
"""


class CSandboxTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="visionsoul-c-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(_env_file=None, local_source_workspace=self.root / "workspace")

    def require_compiler(self):
        if not self.settings.c_sandbox_compiler.is_file():
            self.skipTest("Zig 工具链未安装（tools/install_c_sandbox.py）")

    def jobs_empty(self):
        jobs = self.settings.local_source_workspace / ".sandbox-jobs"
        self.assertEqual(list(jobs.iterdir()), [])

    def test_compiles_and_runs_multiple_cases(self):
        self.require_compiler()
        test = PythonTest(cases=[
            CaseSpec(name="小写转大写", stdin="a", expected="A"),
            CaseSpec(name="大写转小写", stdin="Q", expected="q"),
            CaseSpec(name="非字母不变", stdin="5", expected="5"),
        ])
        result = run_c_sandbox(self.settings, GOOD_CODE, test)
        self.jobs_empty()
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual([row.status for row in result.case_results], ["passed"] * 3)

        wrong = run_c_sandbox(self.settings, GOOD_CODE,
                              PythonTest(cases=[CaseSpec(name="故意错", stdin="a", expected="a")]))
        self.assertEqual(wrong.status, "mismatch")

    def test_compile_error_is_reported_without_running(self):
        self.require_compiler()
        broken = GOOD_CODE.replace('printf("%c\\n", ch);', 'printf("%c\\n", ch)')
        result = run_c_sandbox(self.settings, broken, PythonTest())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.phase, "compile")
        self.assertTrue(result.stderr)
        self.jobs_empty()

    def test_missing_compiler_stops_sandbox(self):
        self.settings.c_sandbox_compiler = self.root / "absent" / "zig.exe"
        with patch("app.services.c_sandbox.subprocess.run") as launch:
            result = run_c_sandbox(self.settings, GOOD_CODE, PythonTest())
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.attempts, 0)
        launch.assert_not_called()

    def test_repairs_at_most_three_times(self):
        failures = [PythonCheck(status="failed", detail="failure", phase="compile",
                                stderr="main.c:3:1: error: expected ';'") for _ in range(4)]
        client = Mock()
        fence = chr(96) * 3
        replies = iter([ModelReply(answer=f"[文件名: main.c]\n{fence}c\nint main(void){{return {n};}}\n{fence}",
                                   model="mock") for n in range(1, 5)])
        client.complete.side_effect = lambda *args, **kwargs: next(replies)
        with patch("app.services.c_sandbox.run_c_sandbox", side_effect=failures) as run:
            result = debug_c(self.settings, client, "task", "int main(void){", PythonTest())
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(result.attempts, 4)
        self.assertIn("自动修复 3 次仍未通过", result.detail)

    def test_sample_data_is_not_sent_with_runtime_failure(self):
        failed = PythonCheck(status="mismatch", detail="diff", phase="run", stdout="USER_ANSWER",
                             case_results=[])
        client = Mock()
        fence = chr(96) * 3
        replies = iter([ModelReply(answer=f"[文件名: main.c]\n{fence}c\nint main(void){{return {n};}}\n{fence}",
                                   model="mock") for n in range(1, 4)])
        client.complete.side_effect = lambda *args, **kwargs: next(replies)
        test = PythonTest(cases=[CaseSpec(name="正常", stdin="a", expected="A")])
        with patch("app.services.c_sandbox.run_c_sandbox", side_effect=[failed] * 4):
            result = debug_c(self.settings, client, "task", GOOD_CODE, test)
        self.assertEqual(client.complete.call_count, 3)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertNotIn("stdout", payload)
        self.assertNotIn("USER_ANSWER", json.dumps(payload))
        self.assertEqual(payload["error_type"], "OutputMismatch")
        self.assertEqual(result.status, "mismatch")


if __name__ == "__main__":
    unittest.main()
