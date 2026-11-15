"""Validate the shape of file-based regression samples before local code delivery."""
import csv
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.source_files import FENCE_BLOCK

ROW_CHECK_MARKER = re.compile(r"^[ \t]*\[逐行验收\][ \t]*$", re.MULTILINE)
BAD_ROW_TASK = re.compile(r"跳过.{0,10}(?:坏行|脏数据|坏数据|坏记录|非法|无效|错误行)|(?:坏行|脏数据|坏数据|坏记录|非法记录|无效记录).{0,16}(?:跳过|继续|警告)")
STOP_ON_BAD_ROW = re.compile(r"(?:禁止|不得|不能|不要|不允许)\s*跳过")


def requests_row_check(reply: str, task: str = "") -> bool:
    if STOP_ON_BAD_ROW.search(task):
        return False
    return bool(ROW_CHECK_MARKER.search(FENCE_BLOCK.sub("", reply.replace("\r\n", "\n"))) or BAD_ROW_TASK.search(task))


class FileRowCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    delimiter: Literal[",", "\t", ";", "|", "space"]
    field_count: int = Field(ge=2, le=32, strict=True)
    header: Literal["none", "count", "columns"]
    bad_row: int = Field(ge=2, le=40, strict=True)
    warning: str = Field(min_length=1, max_length=140)
    after_bad: str = Field(min_length=1, max_length=140)

    def valid_row(self, row: str) -> bool:
        if not row.strip():
            return False
        if self.delimiter == "space":
            return len(row.split()) == self.field_count
        try:
            fields = next(csv.reader([row], delimiter=self.delimiter, strict=True))
        except csv.Error:
            return False
        return len(fields) == self.field_count

    def record_lines(self, content: str) -> list[str] | None:
        lines = content.lstrip("\ufeff").splitlines()
        if self.header == "none":
            return lines
        if not lines:
            return None
        if self.header == "count":
            count = lines[0].strip()
            if not count.isascii() or not count.isdecimal() or (count.lstrip("0") or "0") != str(len(lines) - 1):
                return None
        elif not self.valid_row(lines[0]):
            return None
        return lines[1:]

    def sample_problem(self, content: str, expected: str | None) -> str | None:
        rows = self.record_lines(content)
        if rows is None or self.bad_row >= len(rows):
            return "文件自测需有合法表头，以及合法行、坏行、后续合法行。"
        valid = [self.valid_row(row) for row in rows]
        if valid != [index != self.bad_row for index in range(1, len(rows) + 1)]:
            return "文件自测的分隔符或字段数不一致，需只放一条缺列坏行，并在其后保留合法记录。"
        warning, after_bad = self.warning.strip(), self.after_bad.strip()
        if not expected or not warning or not after_bad or after_bad in warning or warning not in expected or after_bad not in expected:
            return "文件自测的预期输出需同时核对坏行警告和后续合法行产生的结果。"
        return None

    def file_problem(self, content: str) -> str | None:
        rows = self.record_lines(content)
        if rows is None:
            return "所选文件的表头与本题的输入格式不一致。"
        if not any(self.valid_row(row) for row in rows):
            delimiter = {"space": "空白", "\t": "制表符"}.get(self.delimiter, repr(self.delimiter))
            return f"所选文件没有符合本题输入约定的数据行。当前程序预期以{delimiter}分隔的 {self.field_count} 列；请核对原题或数据文件。"
        return None
