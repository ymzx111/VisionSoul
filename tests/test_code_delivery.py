"""交付提示词必须写明填空类题目的复现要求。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.local_ide import CODE_DELIVERY_TASK


class DeliveryPromptTests(unittest.TestCase):
    def test_fill_in_the_blank_rule_is_present(self):
        for phrase in ("填空/补全类代码题", "原样复现骨架", "保留原有", "注释文字与待填标记",
                       "不要另写一份全新的程序", "C、C++ 和 Python"):
            self.assertIn(phrase, CODE_DELIVERY_TASK)

    def test_language_scope_stays_python_c_cpp(self):
        for filename in ("main.c", "main.cpp", "main.py"):
            self.assertIn(filename, CODE_DELIVERY_TASK)


if __name__ == "__main__":
    unittest.main()
