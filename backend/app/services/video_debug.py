"""代码排错的结构化证据和引用检查；不执行用户画面中的程序。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CodeEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    frame_index: int = Field(ge=0, le=60)
    kind: Literal['code', 'error', 'context']
    excerpt: str = Field(min_length=1, max_length=700)
    line_number: int | None = Field(default=None, ge=1, le=1000000)


class DebugCause(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hypothesis: str = Field(min_length=1, max_length=240)
    evidence_ids: list[int] = Field(min_length=1, max_length=4)
    check: str = Field(min_length=1, max_length=240)


class CodeDebugReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['likely', 'insufficient']
    visible_error: str | None = Field(default=None, max_length=500)
    evidence: list[CodeEvidence] = Field(default_factory=list, max_length=4)
    causes: list[DebugCause] = Field(default_factory=list, max_length=3)
    steps: list[str] = Field(default_factory=list, max_length=5)
    suggested_code: str | None = Field(default=None, max_length=2000)
    missing: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def coherent(self):
        for cause in self.causes:
            if any(index < 0 or index >= len(self.evidence) for index in cause.evidence_ids):
                raise ValueError('排错原因引用不存在的证据')
        if self.status == 'likely' and (not self.evidence or not self.causes):
            raise ValueError('暂定诊断缺少证据或原因')
        if self.status == 'insufficient' and not self.missing:
            raise ValueError('信息不足时须说明需要补充什么')
        if any(not value.strip() or len(value) > 300 for value in self.steps + self.missing):
            raise ValueError('排错步骤或缺失信息无效')
        return self

    def check_references(self, frames):
        if any(row.frame_index not in frames for row in self.evidence):
            raise ValueError('排错引用不存在的画面')


def debug_reply(report, frames):
    """保证关键建议进入聊天文本，普通追问可以继续理解，不靠角色自行复述。"""
    times = {frame.index: frame.time_seconds for frame in frames}
    lines = ['代码排错：' + ('暂定原因，修改后请重新运行验证。' if report.status == 'likely' else '现有画面不足以定位。')]
    if report.visible_error:
        lines.append('画面报错：' + report.visible_error)
    for cause in report.causes:
        evidence = '、'.join(f'{times[report.evidence[index].frame_index]:g}秒' for index in cause.evidence_ids)
        lines.append(f'可能原因（{evidence}）：{cause.hypothesis}\n验证：{cause.check}')
    lines.extend(f'{index + 1}. {step}' for index, step in enumerate(report.steps))
    if report.suggested_code:
        lines.append('建议片段（未运行）：\n' + report.suggested_code)
    if report.missing:
        lines.append('需要补充：' + '；'.join(report.missing))
    return '\n'.join(lines)
