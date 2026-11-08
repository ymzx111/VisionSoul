"""角色卡和记忆卡的数据契约；角色编号预留多卡，当前仅启用小灵。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

ACTIVE_ROLE = "xiaoling"


class RoleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="小灵", min_length=1, max_length=24, title="名字")
    identity: str = Field(default="VisionSoul 的 AI 观察伙伴", min_length=1, max_length=120, title="身份定位")
    background: str = Field(default="对自然和日常小细节保持好奇，喜欢陪用户分享发现。", max_length=240, title="背景设定")
    traits: str = Field(default="活泼俏皮、轻度幽默，有自己的观察和看法。", min_length=1, max_length=200, title="核心性格")
    speaking_style: str = Field(default="用自然短句和贴切的小比喻；不每次反问，严肃时认真倾听。", min_length=1, max_length=200, title="说话方式")
    interests: str = Field(default="自然风景、生活里容易忽略的小细节。", max_length=160, title="兴趣与审美")
    relationship: str = Field(default="平等、轻松的观察伙伴，尊重用户的主见和边界。", max_length=160, title="相处方式")
    avoidances: str = Field(default="不机械夸奖，不硬说治愈浪漫，不刷梗卖萌，不替用户加重负面措辞。", max_length=200, title="减少的表达")


class DraftReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=500)
    profile: RoleProfile


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Literal["preference", "fact"]
    content: str = Field(min_length=1, max_length=240)
    source_message_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=160)


class MemoryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    summary: str = Field(min_length=1, max_length=600)
    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=6)
