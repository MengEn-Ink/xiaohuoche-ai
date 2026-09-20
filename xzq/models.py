"""核心数据模型与稿件状态机。

状态机（人在环路，每一步落盘）：
    inbox(素材进筐) -> draft(脚本生成) -> approved(人工审核通过)
        -> image(长图就绪) -> pushed(已进草稿箱) -> published(人工群发)

非法跳转直接抛 IllegalTransition，避免"没审核就渲染""没渲染就推送"之类的越步。
"""

from __future__ import annotations

import enum
import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class ArticleState(str, enum.Enum):
    INBOX = "inbox"  # 素材已收集，尚未成稿
    DRAFT = "draft"  # AI 已生成分屏脚本，待人工审核
    APPROVED = "approved"  # 人工审核通过（checklist 全勾）
    IMAGE = "image"  # 长图已渲染
    PUSHED = "pushed"  # 已推进微信草稿箱
    PUBLISHED = "published"  # 手机端已人工群发（终态）


# 允许的状态跳转：key 为当前态，value 为可前往的下态集合。
# 审核不通过可从 draft 退回 inbox 补素材；推送后若发现问题不可自动回退（微信侧已留痕）。
_TRANSITIONS: dict[ArticleState, set[ArticleState]] = {
    ArticleState.INBOX: {ArticleState.DRAFT},
    ArticleState.DRAFT: {ArticleState.APPROVED, ArticleState.INBOX},
    ArticleState.APPROVED: {ArticleState.IMAGE, ArticleState.DRAFT},
    ArticleState.IMAGE: {ArticleState.PUSHED, ArticleState.APPROVED},
    ArticleState.PUSHED: {ArticleState.PUBLISHED},
    ArticleState.PUBLISHED: set(),
}


class IllegalTransition(RuntimeError):
    """不允许的状态跳转。"""


SCREEN_ROLES = frozenset(
    {
        "cover",
        "headline",
        "preview",
        "race",
        "earlybird",
        "gossip",
        "feeling",
        "review",
        "shirt",
        "item",
        "checklist",
        "end",
    }
)


@dataclass
class Material:
    """一条原始素材：Strava 数据或素材筐里的图片（截图/照片）。"""

    kind: str  # "strava" | "screenshot" | "photo"
    text: str = ""  # OCR 文本 / Strava 数据摘录
    source: str = ""  # 来源（文件路径 / Strava activity id）
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Screen:
    """分屏脚本中的一屏。"""

    role: str  # 取值见 SCREEN_ROLES；writer、修订校验和 renderer 共用该契约
    text: list[str]  # 这屏的文案（多句）
    visual: str = ""  # 配图 / 截图 / 贴纸建议
    data: dict[str, Any] = field(default_factory=dict)  # 需人工填的占位（人名/成绩等）


@dataclass
class Article:
    """一篇稿件，贯穿全链路，可序列化为 JSON 落盘。"""

    article_id: str
    kind: str  # ribao | wanbao | tuanjian
    date: str  # 如 "0913"
    title: str = ""
    state: ArticleState = ArticleState.INBOX
    materials: list[Material] = field(default_factory=list)
    screens: list[Screen] = field(default_factory=list)
    review_checked: list[bool] = field(default_factory=list)  # 与 checklist 一一对应
    review_items: list[str] = field(
        default_factory=list
    )  # 记录勾选项文本，防配置变更误继承
    gen_warnings: list[str] = field(
        default_factory=list
    )  # 出稿自检的提示，供人工审核参考
    image_path: str = ""
    cover_path: str = ""  # 微信封面图（独立渲染 1080×460，不跨入正文屏）
    draft_media_id: str = ""  # 微信草稿 media_id
    content_hash: str = ""  # 当前正文 + 模板指纹
    export_hash: str = ""  # 最近一次成功导出的正文指纹
    render_style: str = ""  # 最近一次导出使用的 renderer.style
    current_revision: str = ""  # 最近修订稿 ID；旧 JSON 缺失时为空
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ---- 状态机 ----
    def can_transition(self, target: ArticleState) -> bool:
        return target in _TRANSITIONS[self.state]

    def transition(self, target: ArticleState) -> None:
        """推进状态；越步或退回非法路径时抛 IllegalTransition。"""
        if target not in _TRANSITIONS[self.state]:
            raise IllegalTransition(
                f"非法状态跳转：{self.state.value} -> {target.value}"
            )
        self.state = target
        self.updated_at = time.time()

    # ---- 审核 ----
    def approve(self, checklist_len: int) -> None:
        """审核通过：必须所有 checklist 项都勾选，且确实生成分屏脚本。"""
        if not self.screens:
            raise IllegalTransition("尚无分屏脚本，不能审核通过")
        if len(self.review_checked) != checklist_len or not all(self.review_checked):
            raise IllegalTransition("审核 checklist 未全部勾选，不能通过")
        self.transition(ArticleState.APPROVED)

    # ---- 序列化 ----
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Article":
        d = dict(d)
        d["state"] = ArticleState(d["state"])
        d["materials"] = [Material(**m) for m in d.get("materials", [])]
        d["screens"] = [Screen(**s) for s in d.get("screens", [])]
        return cls(**d)

    def save(self, path: str | Path) -> Path:
        """原子保存，避免 preview/CLI 并发读取半份稿件。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{p.name}.", suffix=".tmp", dir=p.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(self.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, p)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "Article":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
