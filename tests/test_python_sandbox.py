"""Python 隔离验收、安全边界、多组断言和三次修复策略。"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings
from app.services.deepseek import ModelReply
from app.services.python_sandbox import (
    ApprovedFile, CaseResult, CaseSpec, PythonCheck, PythonTest, debug_python, run_python_sandbox,
)


class PythonSandboxTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="visionsoul-python-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(_env_file=None, local_source_workspace=self.root / "workspace")

    def require_runtime(self):
        if not (self.settings.python_sandbox_runtime / "python.wasm").is_file():
            self.skipTest("CPython WASI runtime is optional")
        try:
            import wasmtime  # noqa: F401
        except ImportError:
            self.skipTest("Wasmtime is optional")

    def check(self, code, test=None):
        result = run_python_sandbox(self.settings, code, test or PythonTest())
        jobs = self.settings.local_source_workspace / ".sandbox-jobs"
        self.assertEqual(list(jobs.iterdir()), [])
        return result

    def test_real_runtime_uses_input_and_checks_expected_output(self):
        self.require_runtime()
        code = 'print(open("sample.txt", encoding="utf-8").read(), int(input()) * 2)\n'
        test = PythonTest(stdin="21\n", expected="你好 42", approved_file=ApprovedFile(filename="sample.txt", content="你好"))
        self.assertEqual(self.check(code, test).status, "passed")
        self.assertEqual(self.check(code, test.model_copy(update={"expected": "你好 43"})).status, "mismatch")
        self.assertEqual(self.check("print(42)").status, "ran")

    def test_real_runtime_denies_host_paths_writes_network_and_processes(self):
        self.require_runtime()
        marker = self.root / "host-marker.txt"
        marker.write_text("CANARY", encoding="utf8")
        code = f'''import os
def blocked(label, operation):
    try:
        operation()
    except (OSError, ImportError, AttributeError, NotImplementedError):
        print(label)
blocked("host", lambda: open({str(marker)!r}).read())
blocked("traversal", lambda: open("../../../../host-marker.txt").read())
blocked("write", lambda: open("new.txt", "w"))
def network():
    import socket
    socket.socket().connect(("127.0.0.1", 8765))
blocked("network", network)
blocked("process", lambda: os.system("exit 0"))
print("secret-visible", "DEEPSEEK_API_KEY" in os.environ)
'''
        expected = "host\ntraversal\nwrite\nnetwork\nprocess\nsecret-visible False"
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "SYNTHETIC-CANARY"}):
            self.assertEqual(self.check(code, PythonTest(expected=expected)).status, "passed")
        self.assertEqual(marker.read_text(encoding="utf8"), "CANARY")

    def test_real_runtime_stops_loops_and_caps_output_and_memory(self):
        self.require_runtime()
        self.assertEqual(self.check("while True:\n    pass\n").status, "limit")
        output = self.check('print("x" * 1000000)')
        self.assertEqual(output.status, "limit")
        self.assertLessEqual(len(output.stdout.encode()), 16 * 1024)
        self.assertIn(self.check("print(len(bytearray(220_000_000)))").status, {"failed", "limit"})

    def test_runtime_missing_never_calls_native_process(self):
        self.settings.python_sandbox_runtime = self.root / "absent"
        with patch("app.services.python_sandbox.subprocess.run") as launch:
            result = run_python_sandbox(self.settings, "print(1)", PythonTest())
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.attempts, 0)
        launch.assert_not_called()

    def test_invalid_file_permission_cannot_supply_paths_or_large_content(self):
        for filename in ("../input.txt", r"C:\data\input.csv", ".env", "program.py", "CON.txt"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                ApprovedFile(filename=filename, content="demo")
        with self.assertRaises(ValueError):
            ApprovedFile(filename="sample.txt", content="测" * 3000)

    def test_real_runtime_runs_multiple_cases(self):
        self.require_runtime()
        code = "a = int(input())\nprint(a * 2)\n"
        test = PythonTest(cases=[
            CaseSpec(name="正常", stdin="21\n", expected="42"),
            CaseSpec(name="零", stdin="0\n", expected="0"),
            CaseSpec(name="负数", stdin="-3\n", expected="-6"),
        ])
        result = self.check(code, test)
        self.assertEqual(result.status, "passed")
        self.assertEqual([row.status for row in result.case_results], ["passed", "passed", "passed"])
        self.assertIn("3 组断言全部通过", result.detail)

        wrong = test.model_copy(update={"cases": [CaseSpec(name="边界", stdin="5\n", expected="11")]})
        mismatch = self.check(code, wrong)
        self.assertEqual(mismatch.status, "mismatch")
        self.assertEqual(mismatch.case_results[0].status, "mismatch")
        self.assertIn("边界", mismatch.detail)

    def test_repairs_up_to_three_times_then_keeps_result(self):
        failures = [PythonCheck(status="failed", detail="failure", stdout="RAW",
                                stderr="NameError: RAW", exit_code=1) for _ in range(4)]
        client = Mock()
        fence = chr(96) * 3
        replies = iter([ModelReply(answer=f"[文件名: main.py]\n{fence}python\nprint({n})\n{fence}", model="mock")
                        for n in range(1, 5)])
        client.complete.side_effect = lambda *args, **kwargs: next(replies)
        with patch("app.services.python_sandbox.run_python_sandbox", side_effect=failures) as run:
            result = debug_python(self.settings, client, "task", "print(unknown)", PythonTest())
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(result.attempts, 4)
        self.assertIn("自动修复 3 次仍未通过", result.detail)

    def test_sample_data_keeps_output_local_but_still_repairs_mismatch(self):
        first = PythonCheck(status="mismatch", detail="diff", stdout="USER_ANSWER", stderr="", exit_code=0,
                            case_results=[CaseResult(name="正常", status="mismatch", stdin="21\n",
                                                     expected="42", stdout="USER_ANSWER")])
        second = PythonCheck(status="passed", detail="ok", stdout="USER_ANSWER", exit_code=0,
                             case_results=[CaseResult(name="正常", status="passed", stdin="21\n",
                                                      expected="42", stdout="USER_ANSWER")])
        client = Mock()
        fence = chr(96) * 3
        client.complete.return_value = ModelReply(
            answer=f"[文件名: main.py]\n{fence}python\nprint(42)\n{fence}", model="mock")
        test = PythonTest(cases=[CaseSpec(name="正常", stdin="21\n", expected="42")])
        with patch("app.services.python_sandbox.run_python_sandbox", side_effect=[first, second]):
            result = debug_python(self.settings, client, "打印结果", "print(0)", test)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.attempts, 2)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertNotIn("stdout", payload)
        self.assertNotIn("stderr", payload)
        self.assertEqual(payload["status"], "mismatch")
        self.assertEqual(payload["failed_cases"], [{"name": "正常", "status": "mismatch"}])
        self.assertTrue(payload["error_type"])

    def test_limit_and_infrastructure_states_never_trigger_repair(self):
        client = Mock()
        for check in (
            PythonCheck(status="limit", detail="limit", stderr="NameError: fake"),
            PythonCheck(status="unavailable", detail="unavailable", attempts=0),
            PythonCheck(status="busy", detail="busy", attempts=0),
        ):
            with patch("app.services.python_sandbox.run_python_sandbox", return_value=check) as run:
                self.assertIs(debug_python(self.settings, client, "task", "code", PythonTest()), check)
                run.assert_called_once()
        client.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
