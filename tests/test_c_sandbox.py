"""C / C++ 隔离编译验收：Wasm 版 Clang 编译成 wasm，WASI 沙箱运行，多组断言与三次修复。"""
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
from app.services.sandbox_output import c_text_output, save_sandbox_output

C_CODE = """#include <stdio.h>
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

CPP_CODE = """#include <iostream>

int main()
{
    char ch;
    if (!(std::cin >> ch)) {
        return 0;
    }
    if (ch >= 'A' && ch <= 'Z') {
        ch = (char)(ch - 'A' + 'a');
    } else if (ch >= 'a' && ch <= 'z') {
        ch = (char)(ch - 'a' + 'A');
    }
    std::cout << ch << std::endl;
    return 0;
}
"""

CASES = [
    CaseSpec(name="小写转大写", stdin="a", expected="A"),
    CaseSpec(name="大写转小写", stdin="Q", expected="q"),
    CaseSpec(name="非字母不变", stdin="5", expected="5"),
]


class CSandboxTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="visionsoul-c-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(_env_file=None, local_source_workspace=self.root / "workspace")

    def require_compiler(self):
        if not self.settings.c_sandbox_compiler.is_file():
            self.skipTest("Wasm 编译器未安装（tools/install_c_sandbox.py）")

    def jobs_empty(self):
        jobs = self.settings.local_source_workspace / ".sandbox-jobs"
        self.assertEqual(list(jobs.iterdir()), [])

    def test_compiles_and_runs_multiple_cases(self):
        self.require_compiler()
        result = run_c_sandbox(self.settings, C_CODE, "c", PythonTest(cases=CASES))
        self.jobs_empty()
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual([row.status for row in result.case_results], ["passed"] * 3)

        wrong = run_c_sandbox(self.settings, C_CODE, "c",
                              PythonTest(cases=[CaseSpec(name="故意错", stdin="a", expected="a")]))
        self.assertEqual(wrong.status, "mismatch")

    def test_file_task_compiles_without_running_before_permission(self):
        self.require_compiler()
        source = '#include <stdio.h>\nint main(void) { puts("must not run"); return 0; }'
        check = run_c_sandbox(self.settings, source, "c", PythonTest(), compile_only=True)
        self.assertEqual((check.status, check.phase, check.stdout), ("awaiting_file", "compile", ""))
        failed = run_c_sandbox(self.settings, source.replace("return 0", "return missing"), "c",
                               PythonTest(), compile_only=True)
        self.assertEqual((failed.status, failed.phase), ("failed", "compile"))
        self.jobs_empty()

    def test_cpp_compiles_and_runs_multiple_cases(self):
        self.require_compiler()
        result = run_c_sandbox(self.settings, CPP_CODE, "cpp", PythonTest(cases=CASES))
        self.jobs_empty()
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual([row.status for row in result.case_results], ["passed"] * 3)

    def test_missing_stdin_pauses_and_probe_preserves_the_actual_word(self):
        self.require_compiler()
        source = '#include <stdio.h>\nint main(void){char word[20];puts("Word:");scanf("%19s",word);puts(word);return 0;}'
        missing = run_c_sandbox(self.settings, source, "c", PythonTest())
        self.assertEqual(missing.status, "awaiting_input", missing)
        self.assertEqual(missing.stdout, "Word:\n")
        probe = run_c_sandbox(self.settings, source, "c",
                              PythonTest(cases=[CaseSpec(stdin="FOUND\n", expected=None)]))
        self.assertEqual(probe.status, "ran", probe)
        self.assertEqual(probe.case_results[0].stdin, "FOUND\n")
        self.assertEqual(probe.stdout, "Word:\nFOUND\n")
        self.jobs_empty()

    def test_no_stdin_keeps_approved_file_reads_working(self):
        self.require_compiler()
        source = '#include <stdio.h>\nint main(void){FILE *f=fopen("d.txt","r");if(!f)return 1;putchar(fgetc(f));return 0;}'
        test = PythonTest(expected="本", approved_file={"filename": "演示使用.txt", "content": "本"})
        source = source.replace("putchar(fgetc(f));", "int ch;while((ch=fgetc(f))!=EOF)putchar(ch);fclose(f);")
        result = run_c_sandbox(self.settings, source, "c", test)
        self.assertEqual(result.status, "passed", result)
        self.assertEqual(result.data_file, "演示使用.txt")
        self.jobs_empty()

    def test_multiple_literal_inputs_do_not_receive_the_same_selected_data(self):
        self.require_compiler()
        code = ('#include <stdio.h>\nint main(void){'
                'FILE *a=fopen("a.txt","r");FILE *b=fopen("b.txt","r");'
                'if (!a || !b) return 1;return 0;}')
        result = run_c_sandbox(self.settings, code, "c",
                               PythonTest(approved_file={"filename": "演示使用.txt", "content": "data"}))
        self.assertEqual((result.status, result.data_file), ("file_unused", None))

    def test_file_task_cannot_present_demo_output_as_user_data(self):
        self.require_compiler()
        source = '#include <stdio.h>\nint main(void){FILE *f=fopen("d.txt","r");if(f)putchar(fgetc(f));else puts("DEMO");return 0;}'
        missing = run_c_sandbox(self.settings, source, "c", PythonTest())
        self.assertEqual((missing.status, missing.stdout), ("awaiting_file", ""))
        test = PythonTest(approved_file={"filename": "d.txt", "content": "Q"})
        loaded = run_c_sandbox(self.settings, source, "c", test)
        self.assertEqual((loaded.status, loaded.data_file, loaded.stdout), ("ran", "d.txt", "Q"))
        ignored = run_c_sandbox(self.settings, '#include <stdio.h>\nint main(void){puts("DEMO");return 0;}', "c", test)
        self.assertEqual((ignored.status, ignored.stdout, ignored.data_file), ("file_unused", "", None))
        self.jobs_empty()

    def test_output_write_failure_is_distinct_from_missing_input(self):
        self.require_compiler()
        source = """#include <stdio.h>
#include <stdlib.h>
int main(void) {
    FILE *in = fopen(getenv("VISIONSOUL_DATA_FILE"), "r");
    if (!in) return 1;
    int value = fgetc(in);
    fclose(in);
    FILE *output = fopen("sorted_books.txt", "w");
    if (!output) return 1;
    fputc(value, output);
    fclose(output);
    return 0;
}
"""
        test = PythonTest(approved_file={"filename": "dataset.txt", "content": "Q"})
        blocked = run_c_sandbox(self.settings, source, "c", test)
        self.assertEqual((blocked.status, blocked.data_file), ("failed", "dataset.txt"))
        self.assertIn("已读取所选数据文件", blocked.detail)
        self.assertIn("尝试写入文件", blocked.detail)
        self.assertEqual(run_c_sandbox(self.settings, source.replace(
            'fopen(getenv("VISIONSOUL_DATA_FILE"), "r")',
            'fopen("sorted_books.txt", "w")',
        ), "c", PythonTest()).status, "failed")

        compatible = source.replace('FILE *output =', 'if (getenv("VISIONSOUL_SANDBOX")) {\n'
                                    '        putchar(value);\n        return 0;\n    }\n    FILE *output =')
        result = run_c_sandbox(self.settings, compatible, "c", test)
        self.assertEqual((result.status, result.stdout, result.data_file), ("ran", "Q", "dataset.txt"))
        runtime, name = c_text_output(source)
        result = save_sandbox_output(self.settings.local_source_workspace, name,
                                     run_c_sandbox(self.settings, runtime, "c", test))
        self.assertEqual(result.status, "ran", result)
        self.assertEqual(Path(result.output_path).name, "sorted_books.txt")
        self.assertEqual(Path(result.output_path).read_text(encoding="utf-8"), "Q")
        self.assertEqual(result.stdout, "")
        self.jobs_empty()

    def test_text_output_fallback_ignores_nonlocal_and_ambiguous_output(self):
        for source in (
            '/* FILE *out=fopen("example.txt","w"); fclose(out); */',
            'FILE *out=fopen("../example.txt","w"); fclose(out);',
            'FILE *out=fopen("NUL.txt","w"); fclose(out);',
            'FILE *out=fopen("a.txt","w"); fclose(out); out=fopen("b.txt","w"); fclose(out);',
        ):
            with self.subTest(source=source):
                self.assertEqual(c_text_output(source), (source, None))

    def test_optional_text_output_keeps_screen_statistics(self):
        raw = PythonCheck(status="ran", detail="已运行",
                          stdout="无高额消费记录\n[VISIONSOUL_NO_OUTPUT_FILE]\n总笔数：1\n")
        result = save_sandbox_output(self.settings.local_source_workspace, "high_consumption.txt", raw)
        self.assertEqual(result.status, "ran")
        self.assertEqual(result.stdout, "无高额消费记录\n总笔数：1\n")
        self.assertIsNone(result.output_path)
        self.assertFalse((self.settings.local_source_workspace / "outputs").exists())

    def test_compile_error_is_reported_without_running(self):
        self.require_compiler()
        broken = C_CODE.replace('printf("%c\\n", ch);', 'printf("%c\\n", ch)')
        result = run_c_sandbox(self.settings, broken, "c", PythonTest())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.phase, "compile")
        self.assertTrue(result.stderr)
        self.jobs_empty()

    def test_compile_cannot_read_existing_host_header(self):
        self.require_compiler()
        marker = self.root / "host-only.h"
        marker.write_text("#define HOST_ONLY 1\n", encoding="utf8")
        header = marker.as_posix()
        code = f'#if __has_include("{header}")\n#error Host file was visible\n#endif\nint main(void){{return 0;}}'
        result = run_c_sandbox(self.settings, code, "c", PythonTest())
        self.assertEqual(result.status, "ran", result.stderr)
        self.jobs_empty()

    def test_missing_node_stops_multiple_cases_without_fallback(self):
        self.require_compiler()
        with patch("app.services.c_sandbox.shutil.which", return_value=None), \
                patch("app.services.c_sandbox.subprocess.run") as launch:
            result = run_c_sandbox(self.settings, C_CODE, "c", PythonTest(cases=CASES))
        self.assertEqual(result.status, "unavailable")
        launch.assert_not_called()
        self.jobs_empty()

    def test_missing_compiler_stops_sandbox(self):
        self.settings.c_sandbox_compiler = self.root / "absent" / "zig.exe"
        with patch("app.services.c_sandbox.subprocess.run") as launch:
            result = run_c_sandbox(self.settings, C_CODE, "c", PythonTest())
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
            result = debug_c(self.settings, client, "task", "int main(void){", "c", PythonTest())
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(result.attempts, 4)
        self.assertIn("自动修复 3 次仍未通过", result.detail)

    def test_cpp_repair_rejects_c_extension(self):
        failure = PythonCheck(status="failed", detail="failure", phase="compile", stderr="main.cpp:1:1: error: x")
        client = Mock()
        fence = chr(96) * 3
        client.complete.return_value = ModelReply(
            answer=f"[文件名: main.c]\n{fence}c\nint main(void){{return 0;}}\n{fence}", model="mock")
        with patch("app.services.c_sandbox.run_c_sandbox", return_value=failure):
            result = debug_c(self.settings, client, "task", CPP_CODE, "cpp", PythonTest())
        self.assertEqual(result.detail, "未取得可用的修复代码，已保留当前结果。")

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
            result = debug_c(self.settings, client, "task", C_CODE, "c", test)
        self.assertEqual(client.complete.call_count, 3)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertNotIn("stdout", payload)
        self.assertNotIn("USER_ANSWER", json.dumps(payload))
        self.assertEqual(payload["error_type"], "OutputMismatch")
        self.assertEqual(result.status, "mismatch")


if __name__ == "__main__":
    unittest.main()
