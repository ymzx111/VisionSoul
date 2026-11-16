"""交付提示词必须写明填空类题目的复现要求。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.local_ide import CODE_DELIVERY_TASK


class DeliveryPromptTests(unittest.TestCase):
    def test_fill_in_the_blank_rule_is_present(self):
        for phrase in ("填空/补全类代码题", "原样复现骨架", "保留原有", "注释文字与待填标记",
                       "不要另写一份全新的程序", "C 和 Python"):
            self.assertIn(phrase, CODE_DELIVERY_TASK)

    def test_language_scope_is_python_and_c(self):
        for filename in ("main.c", "main.py"):
            self.assertIn(filename, CODE_DELIVERY_TASK)
        self.assertNotIn("main.cpp", CODE_DELIVERY_TASK)
        self.assertIn("C++ 等其他语言", CODE_DELIVERY_TASK)
        self.assertIn("当前版本支持 Python 和 C，是否可以改用其中一种？", CODE_DELIVERY_TASK)


if __name__ == "__main__":
    unittest.main()
