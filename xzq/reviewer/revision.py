"""审阅包、版本指纹与不可变修订稿持久化。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Article, ArticleState, SCREEN_ROLES, Screen

REVIEW_SCHEMA_VERSION = 1
REVIEW_STATES = {"reviewing", "changes_requested", "revised", "approved"}
ANNOTATION_STATES = {"open", "resolved"}
MAX_TITLE_LENGTH = 200
MAX_SCREEN_TEXT_ITEMS = 100
MAX_SCREEN_TEXT_LENGTH = 2_000
MAX_VISUAL_LENGTH = 4_000
MAX_DATA_JSON_LENGTH = 64_000
REVISION_FIELDS = {"title", "screens"}
SCREEN_PATCH_FIELDS = {"role", "text", "visual", "data"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """同目录临时文件 + fsync + replace，读者不会看到半份 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def empty_review(article_id: str, content_hash: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "pageId": article_id,
        "revision": 0,
        "sessionState": "reviewing",
        "contentHash": content_hash,
        "generalNote": "",
        "annotations": [],
        "updatedAt": utc_now(),
    }


def validate_review(payload: Any, article_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("审阅包必须是 JSON 对象")
    page_id = payload.get("pageId", article_id)
    legacy_ids = {article_id.split("-", 1)[0], article_id.rsplit("-", 1)[-1], "ribao"}
    if page_id != article_id and page_id not in legacy_ids:
        raise ValueError("审阅包不属于当前稿件")
    if payload.get("schemaVersion", 1) != REVIEW_SCHEMA_VERSION:
        raise ValueError("不支持的审阅包版本")
    annotations = payload.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("annotations 必须是数组")
    seen: set[str] = set()
    normalized = deepcopy(payload)
    normalized["pageId"] = article_id
    normalized["schemaVersion"] = REVIEW_SCHEMA_VERSION
    normalized["revision"] = int(payload.get("revision", 0))
    normalized["generalNote"] = str(payload.get("generalNote", ""))
    state = str(payload.get("sessionState", "reviewing"))
    if state not in REVIEW_STATES:
        raise ValueError("无效的审阅会话状态")
    normalized["sessionState"] = state
    for item in annotations:
        if not isinstance(item, dict):
            raise ValueError("批注必须是对象")
        item_id = item.get("id")
        anchor = item.get("anchor")
        position = item.get("position")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise ValueError("批注 id 缺失或重复")
        seen.add(item_id)
        if not isinstance(anchor, str) or not anchor:
            raise ValueError("批注 anchor 缺失")
        if item.get("status") not in ANNOTATION_STATES:
            raise ValueError("批注状态只能是 open/resolved")
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            raise ValueError("批注内容不能为空")
        if not isinstance(position, dict) or not all(
            isinstance(position.get(axis), (int, float)) and 0 <= position[axis] <= 100
            for axis in ("x", "y")
        ):
            raise ValueError("批注位置必须是 0–100 百分比坐标")
    normalized["annotations"] = annotations
    return normalized


class ReviewStore:
    """兼容平铺 article JSON；审阅和修订放在独立文章目录。"""

    def __init__(self, work_dir: str | Path, article_id: str) -> None:
        self.root = Path(work_dir) / article_id
        self.article_id = article_id
        self.review_path = self.root / "review.json"
        self.revisions_dir = self.root / "article.revisions"
        self.exports_dir = self.root / "exports"
        # 只保证当前 preview 进程内的读改写顺序；README 明确禁止同稿多进程。
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """串行化同一 preview 进程内跨 article/review 的复合更新。"""
        with self._lock:
            yield

    def load(self, content_hash: str = "") -> dict[str, Any]:
        if not self.review_path.exists():
            return empty_review(self.article_id, content_hash)
        try:
            return validate_review(
                json.loads(self.review_path.read_text(encoding="utf-8")),
                self.article_id,
            )
        except json.JSONDecodeError as error:
            raise ValueError(f"review.json 无法解析：{error}") from error

    def save(
        self, payload: dict[str, Any], expected_revision: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            current = self.load()
            incoming = validate_review(payload, self.article_id)
            if expected_revision is None:
                expected_revision = incoming["revision"]
            if expected_revision != current["revision"]:
                raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
            incoming["revision"] = current["revision"] + 1
            incoming["updatedAt"] = utc_now()
            atomic_write_json(self.review_path, incoming)
            return incoming

    def save_server_update(
        self,
        payload: dict[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        """保存服务端复合操作结果；调用方必须传请求开始时读取的 revision。"""
        with self._lock:
            current = self.load()
            if expected_revision != current["revision"]:
                raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
            payload = validate_review(payload, self.article_id)
            payload["revision"] = current["revision"] + 1
            payload["updatedAt"] = utc_now()
            atomic_write_json(self.review_path, payload)
            return payload


def content_hash(article: Article, template_version: str) -> str:
    payload = asdict(article)
    for key in (
        "updated_at",
        "image_path",
        "cover_path",
        "content_hash",
        "export_hash",
        "render_style",
    ):
        payload.pop(key, None)
    raw = json.dumps(
        {"article": payload, "template": template_version},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_revision_changes(article: Article, changes: Any) -> dict[str, Any]:
    """统一校验显式及 LLM 修订 patch；返回可安全应用的深拷贝。"""
    if not isinstance(changes, dict):
        raise ValueError("changes 必须是对象")
    unknown = set(changes) - REVISION_FIELDS
    if unknown:
        raise ValueError(
            f"changes 包含未知字段：{', '.join(sorted(map(str, unknown)))}"
        )

    normalized: dict[str, Any] = {}
    if "title" in changes:
        title = changes["title"]
        if not isinstance(title, str) or len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"title 必须是长度不超过 {MAX_TITLE_LENGTH} 的字符串")
        normalized["title"] = title

    screens = changes.get("screens", {})
    if not isinstance(screens, dict):
        raise ValueError("screens 必须是对象")
    normalized_screens: dict[str, dict[str, Any]] = {}
    for raw_index, raw_patch in screens.items():
        if isinstance(raw_index, bool) or not isinstance(raw_index, (str, int)):
            raise ValueError("screen index 必须是非负整数")
        index_text = str(raw_index)
        if not index_text.isdigit() or str(int(index_text)) != index_text:
            raise ValueError("screen index 必须是规范的非负整数")
        index = int(index_text)
        if not 0 <= index < len(article.screens):
            raise ValueError(f"screen index 越界：{index}")
        if not isinstance(raw_patch, dict):
            raise ValueError(f"screens[{index}] 必须是对象")
        unknown_patch = set(raw_patch) - SCREEN_PATCH_FIELDS
        if unknown_patch:
            raise ValueError(
                f"screens[{index}] 包含未知字段：{', '.join(sorted(map(str, unknown_patch)))}"
            )

        patch: dict[str, Any] = {}
        if "role" in raw_patch:
            role = raw_patch["role"]
            if role not in SCREEN_ROLES:
                raise ValueError(
                    f"screens[{index}].role 必须是受支持角色：{', '.join(sorted(SCREEN_ROLES))}"
                )
            patch["role"] = role
        if "text" in raw_patch:
            text = raw_patch["text"]
            if (
                not isinstance(text, list)
                or len(text) > MAX_SCREEN_TEXT_ITEMS
                or any(
                    not isinstance(item, str) or len(item) > MAX_SCREEN_TEXT_LENGTH
                    for item in text
                )
            ):
                raise ValueError(
                    f"screens[{index}].text 必须是最多 {MAX_SCREEN_TEXT_ITEMS} 项、"
                    f"每项不超过 {MAX_SCREEN_TEXT_LENGTH} 字符的字符串数组"
                )
            patch["text"] = list(text)
        if "visual" in raw_patch:
            visual = raw_patch["visual"]
            if not isinstance(visual, str) or len(visual) > MAX_VISUAL_LENGTH:
                raise ValueError(
                    f"screens[{index}].visual 必须是长度不超过 {MAX_VISUAL_LENGTH} 的字符串"
                )
            patch["visual"] = visual
        if "data" in raw_patch:
            data = raw_patch["data"]
            if not isinstance(data, dict):
                raise ValueError(f"screens[{index}].data 必须是对象")
            try:
                encoded_data = json.dumps(data, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"screens[{index}].data 必须可序列化为 JSON"
                ) from error
            if len(encoded_data.encode("utf-8")) > MAX_DATA_JSON_LENGTH:
                raise ValueError(
                    f"screens[{index}].data 超过 {MAX_DATA_JSON_LENGTH} 字节"
                )
            patch["data"] = deepcopy(data)
        normalized_screens[index_text] = patch
    normalized["screens"] = normalized_screens
    return normalized


def _ensure_revisable_state(article: Article) -> None:
    if article.state not in (
        ArticleState.DRAFT,
        ArticleState.APPROVED,
        ArticleState.IMAGE,
    ):
        raise RuntimeError(f"当前稿件状态 {article.state.value} 不能修订")


def create_revision(
    article: Article,
    store: ReviewStore,
    review: dict[str, Any],
    changes: dict[str, Any] | None = None,
) -> tuple[Article, dict[str, Any]]:
    """完整校验后保存旧版快照，再应用显式修改；绝不静默覆盖。"""
    changes = validate_revision_changes(article, changes)
    _ensure_revisable_state(article)
    revision_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    open_items = [item for item in review["annotations"] if item["status"] == "open"]
    snapshot = {
        "revisionId": revision_id,
        "createdAt": utc_now(),
        "sourceArticle": asdict(article),
        "reviewRevision": review["revision"],
        "requestedChanges": open_items,
        "generalNote": review.get("generalNote", ""),
        "appliedChanges": changes,
    }
    atomic_write_json(store.revisions_dir / f"{revision_id}.json", snapshot)

    revised = Article.from_dict(asdict(article))
    if "title" in changes:
        revised.title = changes["title"]
    for raw_index, patch in changes["screens"].items():
        index = int(raw_index)
        original = revised.screens[index]
        revised.screens[index] = Screen(
            role=patch.get("role", original.role),
            text=patch.get("text", original.text),
            visual=patch.get("visual", original.visual),
            data={**original.data, **patch.get("data", {})},
        )
    revised.current_revision = revision_id
    revised.updated_at = datetime.now(timezone.utc).timestamp()
    invalidate_export(revised)
    return revised, snapshot


def invalidate_export(article: Article) -> None:
    """正文变化后撤销导出和批准，所有 checklist 必须重新人工确认。"""
    _ensure_revisable_state(article)
    article.image_path = ""
    article.cover_path = ""
    article.export_hash = ""
    article.content_hash = ""
    article.render_style = ""
    article.review_checked = []
    if article.state == ArticleState.IMAGE:
        article.transition(ArticleState.APPROVED)
    if article.state == ArticleState.APPROVED:
        article.transition(ArticleState.DRAFT)


def restore_revision(article: Article, store: ReviewStore, revision_id: str) -> Article:
    """保留当前稿快照后恢复指定旧版，回退本身也可追溯。"""
    source_path = store.revisions_dir / f"{revision_id}.json"
    if not source_path.is_file() or source_path.parent != store.revisions_dir:
        raise ValueError("修订版本不存在")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        restored = Article.from_dict(payload["sourceArticle"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("修订版本损坏，无法恢复") from error
    _ensure_revisable_state(article)
    _ensure_revisable_state(restored)
    rollback_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    atomic_write_json(
        store.revisions_dir / f"{rollback_id}.json",
        {
            "revisionId": rollback_id,
            "createdAt": utc_now(),
            "sourceArticle": asdict(article),
            "reason": f"恢复 {revision_id} 前自动保存",
        },
    )
    restored.current_revision = f"restored-{revision_id}"
    restored.updated_at = datetime.now(timezone.utc).timestamp()
    invalidate_export(restored)
    return restored
