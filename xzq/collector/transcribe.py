"""素材图片 OCR：用多模态 LLM 把群聊截图 / Strava 截图转成文字。

照片（photo）只做一句话内容描述；截图（screenshot）逐字转录对话/数据，
作为日报晚报段子和战报硬数据的素材来源。
"""
from __future__ import annotations

from ..llm import LLMClient, LLMNotConfigured
from ..models import Material

_SCREENSHOT_PROMPT = (
    "这是骑行俱乐部公众号素材截图（微信群聊或 Strava）。"
    "请逐字转录图中中文文字与关键数据（人名、功率、PR、时间、距离），"
    "保持原顺序；再用一句话说明这是什么内容。不要编造图中没有的信息。"
)
_PHOTO_PROMPT = (
    "这是骑行活动照片。请用一句话客观描述画面内容（场景、人物在做什么、有没有横幅/号码布等），"
    "不要编造看不清的细节。"
)


def transcribe_materials(materials: list[Material], llm: LLMClient) -> list[Material]:
    """对图片类素材就地补 text；无 LLM 配置时跳过（保留空 text，不报错）。"""
    if not llm.is_configured():
        return materials
    for m in materials:
        if m.kind not in ("screenshot", "photo") or m.text:
            continue
        prompt = _SCREENSHOT_PROMPT if m.kind == "screenshot" else _PHOTO_PROMPT
        try:
            m.text = llm.vision([m.source], prompt).strip()
        except LLMNotConfigured:
            break
        except Exception as e:  # 单张失败不阻断整批，留空由人工补
            m.text = f"[OCR 失败：{e}]"
    return materials
