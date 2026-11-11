"""视频任务的提示组织：通用观察、细看比对、代码排错。"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
CODE_DEBUG_TASK = (PROMPTS / 'video_debug.md').read_text(encoding='utf8').strip()
AUTO_VIDEO_TASK = (PROMPTS / 'video_auto.md').read_text(encoding='utf8').strip()


def task_prompt(base, *, audio_task='', comparison_task='', debug_code=False):
    if debug_code:
        return base + '\n' + audio_task + '\n' + CODE_DEBUG_TASK
    return base + ('\n' + audio_task if audio_task else '') + ('\n' + comparison_task if comparison_task else '')
