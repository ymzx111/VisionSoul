"""模型视频JSON的本地兼容解析与不含原文的失败诊断；不调用模型。"""
import json
from pathlib import Path
import re
from uuid import uuid4

from pydantic import ValidationError


class ResultFormatError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def decode_video_result(answer: str) -> dict:
    text = answer.strip().lstrip("\ufeff")
    # 仅移除完整包裹的Markdown，不从任意说明或残缺JSON中猜补结果。
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        # 部分模型在代码字符串中输出真实换行；允许控制字符不改变原有字符串内容。
        payload = json.loads(text, strict=False)
    except (ValueError, TypeError) as exc:
        raise ResultFormatError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ResultFormatError("invalid_root")
    if "reply" not in payload and isinstance(payload.get("answer"), str):
        payload["reply"] = payload.pop("answer")
    elif "answer" in payload and payload.get("answer") == payload.get("reply"):
        payload.pop("answer")
    code = payload.get("code")
    if isinstance(code, str) and isinstance(payload.get("reply"), str):
        payload.pop("code")
        if code.strip() and code.strip() not in payload["reply"]:
            payload["reply"] += "\n\n```\n" + code.rstrip() + "\n```"
        payload.pop("language", None)  # 不用模型语言标签执行或修改代码。
    # 此字段由服务端按实际发送的画面计算，模型生成的同名字段没有效力。
    payload.pop("detail_selection", None)
    return payload


OBSERVATION_FIELDS = {"frame_index", "keywords", "description", "uncertain"}


def sanitize_video_result(payload: dict) -> dict:
    """修掉可无损恢复的类型与长度问题，其余仍交给严格校验。

    只做「字符串写成单值关键词」「说明条数超限」「概述或回复超长」这三类兜底；
    多余字段、非法观察行、依据条数超限仍按原规则报错，避免把模型漂移静默吞掉。
    """
    data = dict(payload)
    observations = data.get("observations")
    if isinstance(observations, list):
        fixed = []
        for row in observations:
            if isinstance(row, dict) and isinstance(row.get("keywords"), str) and row["keywords"].strip():
                row = {**row, "keywords": [row["keywords"].strip()[:30]]}
            fixed.append(row)
        data["observations"] = fixed
    if isinstance(data.get("evidence_notes"), list):
        data["evidence_notes"] = [str(note)[:120] for note in data["evidence_notes"]][:8]
    for field, limit in (("summary", 240), ("reply", 6000)):
        value = data.get(field)
        if isinstance(value, str) and len(value) > limit:
            data[field] = value[:limit]
    return data


SAFE_FIELDS = {"observations", "frame_index", "keywords", "description", "uncertain", "summary", "reply",
               "comparison", "audio_links", "evidence_notes", "code_debug", "candidate_id", "segment_id",
               "frame_time", "relation", "detail", "content_visible", "target", "category", "verdict",
               "conclusion", "features", "candidates", "missing_features", "next_shot", "basis", "name",
               "supporting_features", "differences", "status", "visible_error", "evidence", "kind", "excerpt",
               "line_number", "causes", "hypothesis", "evidence_ids", "check", "steps", "suggested_code", "missing"}

EVIDENCE_ERRORS = {
    "unknown frame": "引用了不存在的画面", "invalid keyword": "画面关键词为空或过长",
    "missing code debug report": "缺少排错报告", "missing comparison": "缺少比对报告",
    "缺少对白依据": "缺少对白依据", "引用了不存在的对白": "引用了不存在的对白",
    "纯音频依据不能伪造画面": "纯音频依据附带了画面引用",
    "引用了不存在的时间候选": "引用了不存在的音画时间候选",
    "候选编号与附带图号或时间冲突": "音画候选编号与时间冲突",
    "引用画面不在音画候选窗口内": "音画关联超出允许时间窗口",
    "comparison cites unknown frame": "比对报告引用了不存在的画面",
    "candidate cites unknown feature": "比对候选引用了不存在的特征",
    "排错引用不存在的画面": "排错报告引用了不存在的画面",
}


def result_failure(exc, reply, directory: Path, stage: str):
    identifier = uuid4().hex[:12]
    reason = "schema"
    fields = []
    if reply.truncated:
        reason = "truncated"
    elif isinstance(exc, ResultFormatError):
        reason = exc.reason
    elif isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_context=False, include_url=False)[:5]:
            location = ".".join(str(part) if isinstance(part, int) or part in SAFE_FIELDS else "[extra]" for part in error["loc"])
            fields.append({"field": location, "type": error["type"]})
    else:
        reason = "evidence"
    record = {"id": identifier, "stage": stage, "reason": reason, "fields": fields,
              "output_chars": len(reply.answer), "truncated": reply.truncated,
              "usage": reply.usage.model_dump() if reply.usage else None}
    detail = EVIDENCE_ERRORS.get(str(exc), "") if reason == "evidence" else ""
    if detail:
        record["validation_detail"] = detail
    # 不保存模型回复、用户提问、图像/对白、请求头或密钥。
    try:
        destination = directory / "diagnostics"
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"video-result-{identifier}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 保存失败不能覆盖真正的解析错误，更不能触发第二次付费请求。
    messages = {
        "truncated": "模型回复达到输出上限，本轮结果未完整返回。请缩小为一个问题，或减少一次要求生成的代码长度。",
        "invalid_json": "模型返回的JSON格式无法读取，本轮结果未保存。",
        "invalid_root": "模型未返回要求的JSON对象，本轮结果未保存。",
        "schema": "模型返回的字段格式不符合要求，本轮结果未保存。",
        "evidence": "模型返回的画面或音画依据校验未通过，本轮结果未保存。",
    }
    return record, f"{messages[reason]}{detail + '。' if detail else ''}错误编号：{identifier}。本次可能已有用量；不会自动重试。"
