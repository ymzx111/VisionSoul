"""多视角比对的数据契约及旧版清晰度字段兼容，不访问模型或网络。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from PIL import ImageFilter, ImageStat
from app.core.video_policy import MAX_EVIDENCE_FRAMES

DETAIL_VERSION = "detail-v1-1280"


def clarity_score(image) -> float:
    small = image.convert("L").resize((160, 160))
    edges = small.filter(ImageFilter.FIND_EDGES).crop((2, 2, 158, 158))
    return round(ImageStat.Stat(edges).mean[0], 4)


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    frame_index: int = Field(ge=0, lt=MAX_EVIDENCE_FRAMES)
    detail: str = Field(min_length=1, max_length=120)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    supporting_features: list[int] = Field(min_length=1, max_length=5)
    differences: str = Field(min_length=1, max_length=160)


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target: str = Field(min_length=1, max_length=80)
    category: Literal["plant", "place", "object", "scene"]
    verdict: Literal["likely", "insufficient"]
    conclusion: str = Field(min_length=1, max_length=240)
    features: list[Feature] = Field(max_length=5)
    candidates: list[Candidate] = Field(max_length=3)
    missing_features: list[str] = Field(max_length=4)
    next_shot: str = Field(min_length=1, max_length=160)
    basis: Literal["visual_inference"] = "visual_inference"

    def check_references(self, frame_indices):
        if any(feature.frame_index not in frame_indices for feature in self.features):
            raise ValueError("comparison cites unknown frame")
        for candidate in self.candidates:
            if any(index < 0 or index >= len(self.features) for index in candidate.supporting_features):
                raise ValueError("candidate cites unknown feature")
        if self.verdict == "likely" and not self.candidates:
            raise ValueError("likely without candidate")
        if any(not text.strip() or len(text) > 120 for text in self.missing_features):
            raise ValueError("invalid missing feature")


def compact_times(values, interval=0.5):
    """按视频实际采样间隔压缩，兼容旧记录；不连接间断复现。"""
    groups = []
    for value in values:
        # 24/25fps等源帧时间会量化到半秒目标附近，不要求恰好0.500秒。
        if groups and abs(value - groups[-1][-1] - interval) < 0.052:
            groups[-1].append(value)
        else:
            groups.append([value])
    return ";".join(f"{group[0]:g}-{group[-1]:g}" if len(group)>2 else ",".join(f"{value:g}" for value in group) for group in groups)
