"""转写质量启发式；不改原文，不把自动分数冒充正确率。"""
import re
from collections import Counter


def transcript_warnings(audio):
    if audio.status != 'ready' or audio.source == 'corrected':
        return []
    warnings = []
    texts = []
    for segment in audio.segments:
        text = re.sub(r'[\W_]+', '', segment.text).lower()
        texts.append(text)
        reasons = []
        if len(text) >= 24:
            grams = [text[i:i + 6] for i in range(len(text) - 5)]
            counts = Counter(grams)
            if grams and max(counts.values()) >= 4 and len(counts) / len(grams) < 0.55:
                reasons.append('重复片段过多')
        if len(text) >= 16 and len(text) / max(segment.end - segment.start, 0.01) > 35:
            reasons.append('文字密度与识别时长明显不符')
        if reasons:
            warnings.append({'segment_id': segment.id, 'reasons': reasons})
    # 循环也可能被词级时间拆成多个短段，单段检查不足以发现。
    counts = Counter(gram for text in texts for gram in (text[i:i + 6] for i in range(max(0, len(text) - 5))))
    if counts:
        phrase, count = counts.most_common(1)[0]
        affected = [index for index, text in enumerate(texts) if phrase in text]
        if count >= 6 and len(affected) >= 3:
            by_id = {row['segment_id']: row for row in warnings}
            for index in affected:
                row = by_id.setdefault(index, {'segment_id': index, 'reasons': []})
                row['reasons'].append('多段转写循环重复，需回听确认')
            warnings = sorted(by_id.values(), key=lambda row: row['segment_id'])
    return warnings


def trusted_transcript(audio, warnings=None):
    """供模型的副本：保留原分段编号和时间，只遮蔽待核对文字。"""
    warnings = transcript_warnings(audio) if warnings is None else warnings
    ids = {row['segment_id'] for row in warnings}
    if not ids:
        return audio
    copy = audio.model_copy(deep=True)
    for segment in copy.segments:
        if segment.id in ids:
            segment.text = '【此段自动转写待校对，不可据此推断内容；请回听原声】'
    return copy
