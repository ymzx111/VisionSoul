"""多视角比对的数据契约与本地细节帧选择，不访问模型或网络。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from PIL import ImageFilter, ImageStat

DETAIL_VERSION = "detail-v1-1280"


def clarity_score(image) -> float:
    small = image.convert("L").resize((160, 160))
    edges = small.filter(ImageFilter.FIND_EDGES).crop((2, 2, 158, 158))
    return round(ImageStat.Stat(edges).mean[0], 4)


def choose_detail_frames(frames, available, limit=4):
    """分散在时间段内选清晰图；原30张概览均保留，不用近似去重删视角。"""
    eligible = [frame for frame in frames if frame.index in available]
    count = min(limit, len(eligible))
    selected = []
    for index in range(count):
        group = eligible[index * len(eligible) // count:(index + 1) * len(eligible) // count]
        best = max(group, key=lambda frame: (frame.clarity or 0, -frame.index))
        selected.append(best.index)
    return selected


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    frame_index: int = Field(ge=0, le=29)
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
