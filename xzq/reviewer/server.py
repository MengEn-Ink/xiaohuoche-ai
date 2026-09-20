"""基于 ThreadingHTTPServer 的本地预览与审阅 API。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..llm import LLMClient
from ..models import Article, ArticleState
from ..renderer.to_longimage import (
    article_content_hash,
    render_style_html,
    render_long_image,
)
from ..writer.generate import revision_changes_from_review
from .queue import ArticleQueue
from .revision import ReviewStore, create_revision, restore_revision


def article_commit_fingerprint(article: Article) -> str:
    """复合写 CAS 指纹：忽略导出派生字段，保留状态、正文与 checklist。"""
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
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def review_gate(
    article: Article, review: dict[str, Any], checklist: list[str] | None = None
) -> dict[str, Any]:
    reasons: list[str] = []
    open_count = sum(item.get("status") == "open" for item in review["annotations"])
    if open_count:
        reasons.append(f"仍有 {open_count} 条待处理意见")
    if not article.screens:
        reasons.append("稿件没有正文分屏")
    if any(screen.data.get("_offline") for screen in article.screens):
        reasons.append("仍包含离线占位内容")
    required = checklist if checklist is not None else article.review_items
    if required and (
        len(article.review_checked) != len(required) or not all(article.review_checked)
    ):
        reasons.append("发布 checklist 尚未全部勾选")
    return {"passed": not reasons, "reasons": reasons}


def status_payload(
    article: Article,
    review: dict[str, Any],
    checklist: list[str] | None = None,
    renderer_style: str = "card",
) -> dict[str, Any]:
    current_hash = article_content_hash(article, renderer_style)
    export_exists = bool(article.image_path and Path(article.image_path).is_file())
    return {
        "articleId": article.article_id,
        "articleState": article.state.value,
        "sessionState": review.get("sessionState", "reviewing"),
        "contentHash": current_hash,
        "reviewRevision": review["revision"],
        "exportHash": article.export_hash,
        "exportCurrent": (
            export_exists
            and article.render_style == renderer_style
            and article.export_hash == current_hash
        ),
        "checklist": [
            {
                "text": item,
                "checked": index < len(article.review_checked)
                and article.review_checked[index],
            }
            for index, item in enumerate(
                checklist if checklist is not None else article.review_items
            )
        ],
        "gate": review_gate(article, review, checklist),
    }


def make_preview_handler(
    queue: ArticleQueue,
    article_id: str,
    checklist: list[str],
    llm: LLMClient | None = None,
    renderer_style: str = "card",
):
    llm = llm or LLMClient()
    article_path = queue.work_dir / f"{article_id}.json"
    preview_assets = queue.work_dir / f"{article_id}-preview-assets"
    store = ReviewStore(queue.work_dir, article_id)

    class PreviewHandler(BaseHTTPRequestHandler):
        server_version = "XZQPreview/2"

        def _article(self) -> Article:
            return Article.load(article_path)

        def _send(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_000_000:
                raise ValueError("请求体过大")
            raw = self.rfile.read(length)
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return value

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                article = self._article()
                if path in {"/", "/index.html"}:
                    review = store.load(article_content_hash(article, renderer_style))
                    gate = review_gate(article, review, checklist)
                    body = render_style_html(
                        article,
                        renderer_style,
                        preview_assets,
                        include_review=True,
                        gate_passed=gate["passed"],
                    ).encode()
                    self._send(200, body, "text/html; charset=utf-8")
                elif path == "/api/review":
                    self._json(
                        200, store.load(article_content_hash(article, renderer_style))
                    )
                elif path == "/api/status":
                    self._json(
                        200,
                        status_payload(
                            article, store.load(), checklist, renderer_style
                        ),
                    )
                elif path == "/api/export":
                    status = status_payload(
                        article, store.load(), checklist, renderer_style
                    )
                    if not status["exportCurrent"]:
                        self._json(409, {"error": "当前成品已过期，请重新生成"})
                    else:
                        output = Path(article.image_path).read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header(
                            "Content-Disposition",
                            f'attachment; filename="{article.article_id}.png"',
                        )
                        self.send_header("Content-Length", str(len(output)))
                        self.end_headers()
                        self.wfile.write(output)
                elif path == "/events":
                    self._events()
                elif path.startswith(f"/{preview_assets.name}/"):
                    name = Path(path).name
                    asset = preview_assets / name
                    if not name or not asset.is_file():
                        self.send_error(404, "预览资源不存在")
                    else:
                        self._send(
                            200,
                            asset.read_bytes(),
                            mimetypes.guess_type(asset.name)[0]
                            or "application/octet-stream",
                        )
                else:
                    self.send_error(404, "预览资源不存在")
            except (ValueError, RuntimeError, OSError) as error:
                self._json(500, {"error": str(error)})

        def do_PUT(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/review":
                self.send_error(404)
                return
            try:
                payload = self._body()
                saved = store.save(payload, payload.get("revision"))
                self._json(200, saved)
            except RuntimeError as error:
                self._json(409, {"error": str(error), "current": store.load()})
            except (ValueError, json.JSONDecodeError, OSError) as error:
                self._json(400, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                article = self._article()
                review = store.load(article_content_hash(article, renderer_style))
                if path == "/api/revisions":
                    request = self._body()
                    expected_revision = request.get("revision")
                    if type(expected_revision) is not int:
                        raise ValueError("revision 必须是请求开始时的整数版本")
                    if expected_revision != review["revision"]:
                        raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
                    if "changes" in request:
                        changes = request["changes"]
                    else:
                        changes = revision_changes_from_review(
                            article,
                            review["annotations"],
                            review.get("generalNote", ""),
                            llm,
                        )
                    with store.transaction():
                        # 两个指纹检查都先于 patch 校验后的修订快照和 article 落盘。
                        if store.load()["revision"] != expected_revision:
                            raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
                        if article_commit_fingerprint(
                            self._article()
                        ) != article_commit_fingerprint(article):
                            raise RuntimeError("稿件已被其他操作更新，请刷新后重试")
                        revised, snapshot = create_revision(
                            article, store, review, changes
                        )
                        review["sessionState"] = "revised"
                        review["contentHash"] = article_content_hash(
                            revised, renderer_style
                        )
                        queue.save(revised)
                        try:
                            saved_review = store.save_server_update(
                                review, expected_revision
                            )
                        except Exception:
                            queue.save(article)
                            raise
                    self._json(
                        201,
                        {
                            "revisionId": snapshot["revisionId"],
                            "contentHash": review["contentHash"],
                            "review": saved_review,
                        },
                    )
                elif path == "/api/revisions/restore":
                    request = self._body()
                    expected_revision = request.get("revision")
                    if type(expected_revision) is not int:
                        raise ValueError("revision 必须是请求开始时的整数版本")
                    if expected_revision != review["revision"]:
                        raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
                    revision_id = str(request.get("revisionId", ""))
                    with store.transaction():
                        if store.load()["revision"] != expected_revision:
                            raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
                        if article_commit_fingerprint(
                            self._article()
                        ) != article_commit_fingerprint(article):
                            raise RuntimeError("稿件已被其他操作更新，请刷新后重试")
                        restored = restore_revision(article, store, revision_id)
                        review["sessionState"] = "changes_requested"
                        review["contentHash"] = article_content_hash(
                            restored, renderer_style
                        )
                        queue.save(restored)
                        try:
                            saved = store.save_server_update(review, expected_revision)
                        except Exception:
                            queue.save(article)
                            raise
                    self._json(
                        200, {"contentHash": review["contentHash"], "review": saved}
                    )
                elif path == "/api/checklist":
                    request = self._body()
                    checked = request.get("checked")
                    if (
                        not isinstance(checked, list)
                        or len(checked) != len(checklist)
                        or any(type(value) is not bool for value in checked)
                    ):
                        raise ValueError("checked 必须是与 checklist 等长的布尔数组")
                    with store.transaction():
                        article = self._article()
                        queue.init_checklist(article, checklist)
                        article.review_checked = checked
                        if not all(checked):
                            if article.state == ArticleState.IMAGE:
                                article.transition(ArticleState.APPROVED)
                            if article.state == ArticleState.APPROVED:
                                article.transition(ArticleState.DRAFT)
                        queue.save(article)
                    self._json(
                        200,
                        status_payload(
                            article, store.load(), checklist, renderer_style
                        ),
                    )
                elif path == "/api/review/approve":
                    request = self._body()
                    expected_revision = request.get("revision")
                    if type(expected_revision) is not int:
                        raise ValueError("revision 必须是请求开始时的整数版本")
                    gate_error = ""
                    with store.transaction():
                        # 与 checklist 更新共用同一进程锁；锁内只使用重新读取的最新状态。
                        article = self._article()
                        review = store.load(
                            article_content_hash(article, renderer_style)
                        )
                        if review["revision"] != expected_revision:
                            raise RuntimeError("审阅包已被其他客户端更新，请刷新后重试")
                        queue.init_checklist(article, checklist)
                        gate = review_gate(article, review, checklist)
                        if not gate["passed"]:
                            gate_error = "；".join(gate["reasons"])
                        else:
                            original = self._article()
                            if article.state == ArticleState.DRAFT:
                                article.approve(len(checklist))
                            elif article.state not in (
                                ArticleState.APPROVED,
                                ArticleState.IMAGE,
                            ):
                                raise RuntimeError(
                                    f"当前稿件状态 {article.state.value} 不能批准"
                                )
                            review["sessionState"] = "approved"
                            queue.save(article)
                            try:
                                saved = store.save_server_update(
                                    review, expected_revision
                                )
                            except Exception:
                                queue.save(original)
                                raise
                    if gate_error:
                        self._json(409, {"error": gate_error})
                        return
                    self._json(
                        200,
                        {
                            "review": saved,
                            "status": status_payload(
                                article, saved, checklist, renderer_style
                            ),
                        },
                    )
                elif path == "/api/export":
                    gate_error = ""
                    with store.transaction():
                        source_article = self._article()
                        source_review = store.load(
                            article_content_hash(source_article, renderer_style)
                        )
                        gate = review_gate(source_article, source_review, checklist)
                        if (
                            not gate["passed"]
                            or source_review.get("sessionState") != "approved"
                        ):
                            gate_error = "审阅未批准或门禁未通过"
                        source_fingerprint = article_commit_fingerprint(source_article)
                        source_review_revision = source_review["revision"]
                    if gate_error:
                        self._json(
                            409, {"error": gate_error, "reasons": gate["reasons"]}
                        )
                        return

                    store.root.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix=".export-", dir=store.root
                    ) as temporary:
                        rendered = Article.from_dict(asdict(source_article))
                        render_long_image(rendered, temporary, style=renderer_style)
                        with store.transaction():
                            # 截图期间允许其他请求运行；提交时对 article+review 再做一次 CAS/gate。
                            latest_article = self._article()
                            latest_review = store.load(
                                article_content_hash(latest_article, renderer_style)
                            )
                            if (
                                article_commit_fingerprint(latest_article)
                                != source_fingerprint
                                or latest_review["revision"] != source_review_revision
                            ):
                                raise RuntimeError(
                                    "导出期间稿件或审阅已更新，请重新导出"
                                )
                            latest_gate = review_gate(
                                latest_article, latest_review, checklist
                            )
                            if (
                                not latest_gate["passed"]
                                or latest_review.get("sessionState") != "approved"
                            ):
                                raise RuntimeError(
                                    "导出期间审批或门禁已失效，请重新审批"
                                )

                            store.exports_dir.mkdir(parents=True, exist_ok=True)
                            final_image = store.exports_dir / f"{article_id}.png"
                            final_cover = store.exports_dir / f"{article_id}-cover.jpg"
                            final_html = store.exports_dir / f"{article_id}.html"
                            os.replace(rendered.image_path, final_image)
                            os.replace(rendered.cover_path, final_cover)
                            temporary_html = Path(temporary) / f"{article_id}.html"
                            if temporary_html.is_file():
                                os.replace(temporary_html, final_html)
                            rendered.image_path = str(final_image)
                            rendered.cover_path = str(final_cover)
                            queue.save(rendered)
                    self._json(
                        201,
                        {
                            "path": rendered.image_path,
                            "exportHash": rendered.export_hash,
                        },
                    )
                else:
                    self.send_error(404)
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"error": str(error)})
            except (RuntimeError, OSError) as error:
                self._json(409, {"error": str(error)})

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            watched = {"content": article_path, "review": store.review_path}
            mtimes = {
                name: path.stat().st_mtime_ns if path.exists() else 0
                for name, path in watched.items()
            }
            try:
                self.wfile.write(b"event: status\ndata: connected\n\n")
                self.wfile.flush()
                for _ in range(120):
                    time.sleep(1)
                    sent = False
                    for name, path in watched.items():
                        current = path.stat().st_mtime_ns if path.exists() else 0
                        if current != mtimes[name]:
                            mtimes[name] = current
                            self.wfile.write(
                                f"event: {name}\ndata: updated\n\n".encode()
                            )
                            sent = True
                    if not sent:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *values: object) -> None:
            print(f"[preview] {format % values}")

    return PreviewHandler
