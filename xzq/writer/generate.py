"""分屏脚本生成：调 LLM 出稿；无 LLM 配置时离线出模板骨架（占位）。

离线兜底保证无密钥也能跑通 pipeline、便于联调排版与状态机，
但骨架里的文案/数据都是占位，绝不可直接发布。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..llm import LLMClient
from ..models import Article, SCREEN_ROLES, Screen
from ..reviewer.revision import validate_revision_changes
from . import prompts
from .style import StyleAssets


_COPY_PUNCTUATION = re.compile(r"[\s，。！？、；：,.!?;:'\"‘’“”·—-]+")


def _deduplicate_screen_copy(lines: list[object]) -> list[str]:
    """Keep the first wording when a model repeats it with punctuation changes."""
    output: list[str] = []
    seen: set[str] = set()
    for value in lines:
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        key = _COPY_PUNCTUATION.sub("", text).casefold()
        if key and key not in seen:
            output.append(text)
            seen.add(key)
    return output


def generate_script(
    article: Article,
    assets: StyleAssets,
    llm: LLMClient,
    offline: bool = False,
) -> Article:
    """就地填充 article.title / article.screens，并把状态推进到 draft。"""
    system = prompts.build_system_prompt(assets)
    user = prompts.build_user_prompt(
        assets, article.kind, article.date, article.materials, article.title
    )

    if offline or not llm.is_configured():
        data = _offline_skeleton(article.kind)
    else:
        raw = llm.chat(system, user, temperature=0.9)
        data = _parse_json(raw)

    article.title = data.get("title") or article.title or f"{article.date} 小火车日报"
    screens = data.get("screens", [])
    if not isinstance(screens, list) or any(
        not isinstance(screen, dict) for screen in screens
    ):
        raise ValueError("模型输出的 screens 必须是对象数组")
    invalid_roles = [
        screen.get("role")
        for screen in screens
        if screen.get("role", "end") not in SCREEN_ROLES
    ]
    if invalid_roles:
        raise ValueError(f"模型输出包含不支持的 role：{invalid_roles}")
    article.screens = [
        Screen(
            role=s.get("role", "end"),
            text=_deduplicate_screen_copy(
                s.get("text", [])
                if isinstance(s.get("text"), list)
                else [s.get("text", "")]
            ),
            visual=s.get("visual", ""),
            data=s.get("data", {}) or {},
        )
        for s in screens
    ]
    if not (offline or not llm.is_configured()):
        _refit_visuals(article, llm)
    from . import quality

    input_text = "\n".join(
        m.text for m in article.materials if m.kind == "draft" and m.text
    )
    article.gen_warnings = quality.check(article, assets, input_text)
    _to_draft(article)
    return article


def _refit_visuals(article: Article, llm: LLMClient) -> None:
    """第二遍只复核配图和贴纸：writer 一遍写完常把素材配到不相干的屏上（配图靠描述猜）。
    低温度、只允许改 visual；返回条数对不上或解析失败就保留原样，不影响出稿。"""
    if not article.screens:
        return
    system, user = prompts.build_visual_fix_prompt(article.materials, article.screens)
    try:
        data = _parse_json(llm.chat(system, user, temperature=0.2))
    except Exception:
        return
    fixed = data.get("screens") or []
    if len(fixed) != len(article.screens):
        return
    for s, f in zip(article.screens, fixed):
        # 单个元素坏掉时只保留该屏原 visual，不能让整次出稿因 AttributeError 失败。
        if not isinstance(f, dict):
            continue
        v = f.get("visual")
        if isinstance(v, str) and v.strip():
            s.visual = v.strip()


def _to_draft(article: Article) -> None:
    """把稿件置为 draft 态。

    - INBOX -> DRAFT：首次成稿（合法跳转）。
    - APPROVED -> DRAFT：审核后打回重写（合法跳转）。
    - 已在 DRAFT：重新生成/微调，状态不变，只刷新时间戳，避免 DRAFT->DRAFT 非法跳转。
    """
    from ..models import ArticleState

    if article.state == ArticleState.DRAFT:
        article.updated_at = time.time()
        return
    article.transition(ArticleState.DRAFT)


def _parse_json(raw: str) -> dict[str, Any]:
    """从模型输出里抽取分屏脚本 JSON。

    模型有时先写一段思考再给 JSON，思考里可能夹着小的 {…}；取「第一个 {」会解析到碎片。
    所以从最后一个 } 往前，逐个 { 试解析，第一个能解析出带 screens 的对象就是正文。
    """
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    end = raw.rfind("}")
    if end == -1:
        raise ValueError(f"模型未返回合法 JSON：{raw[:200]}")
    starts = [i for i, ch in enumerate(raw[: end + 1]) if ch == "{"]
    for start in starts:  # 从最早的 { 开始试：完整对象一定从某个 { 起、到最后的 } 止
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "screens" in obj:
            return obj
    raise ValueError(f"模型输出里找不到带 screens 的 JSON：{raw[:200]}")


def _offline_skeleton(kind: str) -> dict[str, Any]:
    """离线骨架：按栏目给出屏结构与占位提示，文案留空等人工/LLM 填。"""
    roles = {
        # 日报顺序固定对齐 0908 最终公众号范本；某期没有对应素材时可删屏，禁止拿旧图凑数。
        "ribao": [
            "cover",
            "feeling",
            "gossip",
            "race",
            "review",
            "shirt",
            "preview",
            "end",
        ],
        "wanbao": ["cover", "gossip", "gossip", "gossip", "end"],
        "tuanjian": ["cover", "preview", "checklist", "end"],
        "subao": ["headline", "item", "item", "item", "item"],
        "zaobao": ["headline", "gossip", "earlybird", "gossip"],
        "kuaixun": ["cover", "gossip", "gossip", "end"],
    }.get(kind, ["cover", "end"])
    screens = [
        {
            "role": r,
            "text": ["[离线占位：接入 LLM 后由 AI 按风格生成]"],
            "visual": "[待配图/截图/贴纸]",
            "data": {"_offline": True},
        }
        for r in roles
    ]
    return {"title": "", "screens": screens}


def revision_changes_from_review(
    article: Article,
    annotations: list[dict[str, Any]],
    general_note: str,
    llm: LLMClient,
) -> dict[str, Any]:
    """将待处理意见转换为受限 patch；无 LLM 时保留快照供人工复核，不臆改正文。"""
    if not annotations and not general_note.strip():
        raise ValueError("没有待处理意见，无法生成修订稿")
    if not llm.is_configured():
        return {}
    current = {
        "title": article.title,
        "screens": [
            {
                "index": index,
                "role": screen.role,
                "text": screen.text,
                "visual": screen.visual,
                "data": screen.data,
            }
            for index, screen in enumerate(article.screens)
        ],
    }
    requirements = {
        "generalNote": general_note,
        "annotations": [
            {"anchor": item["anchor"], "text": item["text"], "id": item["id"]}
            for item in annotations
            if item.get("status") == "open"
        ],
    }
    raw = llm.chat(
        "你是日报修订器。只返回 JSON patch；只改批注锚点对应屏，禁止增删屏或改无关内容。",
        "当前稿件：\n"
        + json.dumps(current, ensure_ascii=False)
        + "\n待处理意见：\n"
        + json.dumps(requirements, ensure_ascii=False)
        + '\n返回 {"title": 可选字符串, "screens": {"屏下标": {"text": [...], "visual": "...", "data": {...}}}}。',
        temperature=0.2,
    )
    changes = validate_revision_changes(article, _parse_json(raw))
    allowed = {
        str(int(item["anchor"].split("-")[-1]) - 1)
        for item in annotations
        if item.get("status") == "open"
        and str(item.get("anchor", "")).startswith("screen-")
    }
    changes["screens"] = {
        str(key): value
        for key, value in changes["screens"].items()
        if str(key) in allowed
    }
    return changes
