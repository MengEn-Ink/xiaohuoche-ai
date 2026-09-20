from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from xzq.models import Article, ArticleState, Screen
from xzq.renderer.to_longimage import article_content_hash
from xzq.reviewer.queue import ArticleQueue
from xzq.reviewer.revision import ReviewStore, empty_review
from xzq.reviewer.server import make_preview_handler


def _request(base: str, path: str, method: str = "GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=3) as response:
        content = response.read()
        return response.status, json.loads(content) if "json" in response.headers.get(
            "Content-Type", ""
        ) else content


def test_review_get_put_status_and_conflict(tmp_path):
    queue = ArticleQueue(tmp_path)
    article = Article(
        "0914-ribao",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["正文"])],
    )
    queue.save(article)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, [])
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        _, review = _request(base, "/api/review")
        now = "2026-09-09T00:00:00+00:00"
        review["annotations"] = [
            {
                "id": "a1",
                "anchor": "screen-01",
                "position": {"x": 12, "y": 20},
                "text": "改短",
                "status": "open",
                "createdAt": now,
                "updatedAt": now,
            }
        ]
        _, saved = _request(base, "/api/review", "PUT", review)
        assert saved["revision"] == 1
        assert (
            json.loads((tmp_path / article.article_id / "review.json").read_text())[
                "annotations"
            ][0]["id"]
            == "a1"
        )
        _, status = _request(base, "/api/status")
        assert status["reviewRevision"] == 1
        assert status["gate"]["passed"] is False
        try:
            _request(base, "/api/review", "PUT", review)
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("stale revision must conflict")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stale_png_download_is_blocked(tmp_path):
    queue = ArticleQueue(tmp_path)
    article = Article(
        "0914-ribao",
        "ribao",
        "0914",
        state=ArticleState.IMAGE,
        screens=[Screen("cover", ["新正文"])],
        export_hash="old",
    )
    article.image_path = str(tmp_path / "old.png")
    (tmp_path / "old.png").write_bytes(b"png")
    queue.save(article)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, [])
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        try:
            _request(f"http://127.0.0.1:{server.server_port}", "/api/export")
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("stale export must not download")
    finally:
        server.shutdown()
        server.server_close()


def test_revision_conflict_does_not_overwrite_concurrent_review_or_article(
    tmp_path, monkeypatch
):
    import xzq.reviewer.server as server_module

    queue = ArticleQueue(tmp_path)
    article = Article(
        "race-ribao",
        "ribao",
        "0914",
        title="旧标题",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["正文"])],
    )
    queue.save(article)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, [])
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    entered = threading.Event()
    release = threading.Event()

    def delayed_changes(*_args):
        entered.set()
        assert release.wait(3)
        return {"title": "不应提交的修订"}

    monkeypatch.setattr(server_module, "revision_changes_from_review", delayed_changes)
    try:
        _, review = _request(base, "/api/review")
        now = "2026-09-09T00:00:00+00:00"
        review["annotations"] = [
            {
                "id": "a1",
                "anchor": "screen-01",
                "position": {"x": 1, "y": 2},
                "text": "改标题",
                "status": "open",
                "createdAt": now,
                "updatedAt": now,
            }
        ]
        _, review = _request(base, "/api/review", "PUT", review)
        result = {}

        def revise():
            try:
                _request(
                    base, "/api/revisions", "POST", {"revision": review["revision"]}
                )
            except HTTPError as error:
                result["status"] = error.code

        worker = threading.Thread(target=revise)
        worker.start()
        assert entered.wait(3)
        concurrent = dict(review)
        concurrent["generalNote"] = "并发保存的新说明"
        _request(base, "/api/review", "PUT", concurrent)
        release.set()
        worker.join(timeout=3)

        assert result["status"] == 409
        assert queue.get(article.article_id).title == "旧标题"
        assert _request(base, "/api/review")[1]["generalNote"] == "并发保存的新说明"
        assert not list(
            (tmp_path / article.article_id / "article.revisions").glob("*.json")
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_checklist_api_persists_to_article_and_unblocks_gate(tmp_path):
    queue = ArticleQueue(tmp_path)
    article = Article(
        "check-ribao",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["正文"])],
    )
    queue.save(article)
    items = ["事实准确", "授权完成"]
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, items)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        _, initial = _request(base, "/api/status")
        assert initial["checklist"] == [
            {"text": "事实准确", "checked": False},
            {"text": "授权完成", "checked": False},
        ]
        _, status = _request(base, "/api/checklist", "POST", {"checked": [True, True]})
        assert status["gate"]["passed"] is True
        saved = queue.get(article.article_id)
        assert saved.review_items == items
        assert saved.review_checked == [True, True]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "changes",
    [
        None,
        {"title": 7},
        {"screens": []},
        {"screens": {"9": {"text": ["越界"]}}},
        {"screens": {"0": {"text": "不是数组"}}},
        {"screens": {"0": {"visual": []}}},
        {"screens": {"0": {"data": []}}},
    ],
)
def test_invalid_revision_patch_returns_400_without_any_write(tmp_path, changes):
    queue = ArticleQueue(tmp_path)
    article = Article(
        "invalid-ribao",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["原文"])],
    )
    queue.save(article)
    article_path = tmp_path / f"{article.article_id}.json"
    before = article_path.read_bytes()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, [])
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        try:
            _request(
                base, "/api/revisions", "POST", {"revision": 0, "changes": changes}
            )
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("invalid patch must return 400")
        assert article_path.read_bytes() == before
        assert not (tmp_path / article.article_id / "review.json").exists()
        assert not list(
            (tmp_path / article.article_id / "article.revisions").glob("*.json")
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("initial_state", [ArticleState.APPROVED, ArticleState.IMAGE])
def test_revision_of_approved_article_requires_checklist_and_approval_again(
    tmp_path, initial_state
):
    queue = ArticleQueue(tmp_path)
    items = ["事实准确", "授权完成"]
    article = Article(
        "reapprove-ribao",
        "ribao",
        "0914",
        state=initial_state,
        screens=[Screen("cover", ["旧正文"])],
        review_items=items,
        review_checked=[True, True],
    )
    queue.save(article)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, items)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        _, result = _request(
            base,
            "/api/revisions",
            "POST",
            {
                "revision": 0,
                "changes": {"screens": {"0": {"text": ["新正文"]}}},
            },
        )
        revised = queue.get(article.article_id)
        assert revised.state == ArticleState.DRAFT
        assert revised.review_checked == []
        assert result["review"]["sessionState"] == "revised"

        try:
            _request(
                base,
                "/api/review/approve",
                "POST",
                {"revision": result["review"]["revision"]},
            )
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("revision must require checklist confirmation")

        _request(base, "/api/checklist", "POST", {"checked": [True, True]})
        _, approved = _request(
            base,
            "/api/review/approve",
            "POST",
            {"revision": result["review"]["revision"]},
        )
        assert approved["status"]["articleState"] == "approved"
        assert queue.get(article.article_id).state == ArticleState.APPROVED
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_concurrent_checklist_cancel_is_not_lost_by_approval(tmp_path, monkeypatch):
    import xzq.reviewer.server as server_module

    queue = ArticleQueue(tmp_path)
    items = ["事实准确", "授权完成"]
    article = Article(
        "approve-race",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["正文"])],
        review_items=items,
        review_checked=[True, True],
    )
    queue.save(article)

    gate_entered = threading.Event()
    release_gate = threading.Event()
    cancel_parsed = threading.Event()
    delay_gate = threading.Event()
    original_gate = server_module.review_gate

    def controlled_gate(*args, **kwargs):
        result = original_gate(*args, **kwargs)
        if delay_gate.is_set() and not gate_entered.is_set():
            assert result["passed"] is True
            gate_entered.set()
            assert release_gate.wait(3)
        return result

    monkeypatch.setattr(server_module, "review_gate", controlled_gate)
    handler = make_preview_handler(queue, article.article_id, items)
    original_body = handler._body

    def controlled_body(self):
        body = original_body(self)
        if self.path == "/api/checklist":
            cancel_parsed.set()
        return body

    monkeypatch.setattr(handler, "_body", controlled_body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    results = {}
    try:
        delay_gate.set()

        def approve():
            results["approve"] = _request(
                base, "/api/review/approve", "POST", {"revision": 0}
            )[0]

        def cancel():
            results["cancel"] = _request(
                base, "/api/checklist", "POST", {"checked": [True, False]}
            )[0]

        approve_thread = threading.Thread(target=approve)
        approve_thread.start()
        assert gate_entered.wait(3)
        cancel_thread = threading.Thread(target=cancel)
        cancel_thread.start()
        assert cancel_parsed.wait(3)
        release_gate.set()
        approve_thread.join(timeout=3)
        cancel_thread.join(timeout=3)

        final = queue.get(article.article_id)
        assert results == {"approve": 200, "cancel": 200}
        assert final.state == ArticleState.DRAFT
        assert final.review_checked == [True, False]
    finally:
        release_gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_approval_rereads_gate_after_concurrent_checklist_cancel(tmp_path, monkeypatch):
    queue = ArticleQueue(tmp_path)
    items = ["事实准确", "授权完成"]
    article = Article(
        "approve-stale-gate",
        "ribao",
        "0914",
        state=ArticleState.DRAFT,
        screens=[Screen("cover", ["正文"])],
        review_items=items,
        review_checked=[True, True],
    )
    queue.save(article)
    handler = make_preview_handler(queue, article.article_id, items)
    original_body = handler._body
    approval_parsed = threading.Event()
    release_approval = threading.Event()

    def controlled_body(self):
        body = original_body(self)
        if self.path == "/api/review/approve":
            approval_parsed.set()
            assert release_approval.wait(3)
        return body

    monkeypatch.setattr(handler, "_body", controlled_body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    result = {}
    try:

        def approve():
            try:
                _request(base, "/api/review/approve", "POST", {"revision": 0})
            except HTTPError as error:
                result["approve"] = error.code

        approve_thread = threading.Thread(target=approve)
        approve_thread.start()
        assert approval_parsed.wait(3)
        assert (
            _request(base, "/api/checklist", "POST", {"checked": [True, False]})[0]
            == 200
        )
        release_approval.set()
        approve_thread.join(timeout=3)

        final = queue.get(article.article_id)
        assert result["approve"] == 409
        assert final.state == ArticleState.DRAFT
        assert final.review_checked == [True, False]
    finally:
        release_approval.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_export_does_not_overwrite_concurrent_checklist_cancel(tmp_path, monkeypatch):
    import xzq.reviewer.server as server_module

    queue = ArticleQueue(tmp_path)
    items = ["事实准确", "授权完成"]
    article = Article(
        "export-race",
        "ribao",
        "0914",
        state=ArticleState.APPROVED,
        screens=[Screen("cover", ["正文"])],
        review_items=items,
        review_checked=[True, True],
    )
    queue.save(article)
    store = ReviewStore(tmp_path, article.article_id)
    review = empty_review(article.article_id, article_content_hash(article))
    review["sessionState"] = "approved"
    store.save(review)

    render_entered = threading.Event()
    release_render = threading.Event()

    def controlled_render(rendered, output_dir, style="card"):
        render_entered.set()
        assert release_render.wait(3)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        image = output / f"{rendered.article_id}.png"
        image.write_bytes(b"png")
        cover = output / f"{rendered.article_id}-cover.jpg"
        cover.write_bytes(b"cover")
        (output / f"{rendered.article_id}.html").write_text("html", encoding="utf-8")
        rendered.image_path = str(image)
        rendered.cover_path = str(cover)
        rendered.transition(ArticleState.IMAGE)
        rendered.render_style = style
        rendered.content_hash = article_content_hash(rendered, style)
        rendered.export_hash = rendered.content_hash
        return str(image)

    monkeypatch.setattr(server_module, "render_long_image", controlled_render)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, items)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    result = {}
    try:

        def export():
            try:
                _request(base, "/api/export", "POST", {})
            except HTTPError as error:
                result["export"] = error.code

        export_thread = threading.Thread(target=export)
        export_thread.start()
        assert render_entered.wait(3)
        assert (
            _request(base, "/api/checklist", "POST", {"checked": [True, False]})[0]
            == 200
        )
        release_render.set()
        export_thread.join(timeout=3)

        final = queue.get(article.article_id)
        assert result["export"] == 409
        assert final.state == ArticleState.DRAFT
        assert final.review_checked == [True, False]
        assert final.image_path == final.cover_path == final.export_hash == ""
        assert not store.exports_dir.exists()
        assert not list(store.root.glob(".export-*"))
    finally:
        release_render.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_revision_does_not_overwrite_concurrent_checklist_cancel(tmp_path, monkeypatch):
    import xzq.reviewer.server as server_module

    queue = ArticleQueue(tmp_path)
    items = ["事实准确"]
    article = Article(
        "revision-check-race",
        "ribao",
        "0914",
        state=ArticleState.APPROVED,
        screens=[Screen("cover", ["旧正文"])],
        review_items=items,
        review_checked=[True],
    )
    queue.save(article)
    store = ReviewStore(tmp_path, article.article_id)
    review = empty_review(article.article_id, article_content_hash(article))
    review["generalNote"] = "修改正文"
    saved_review = store.save(review)

    generation_entered = threading.Event()
    release_generation = threading.Event()

    def controlled_changes(*_args):
        generation_entered.set()
        assert release_generation.wait(3)
        return {"screens": {"0": {"text": ["不应写入"]}}}

    monkeypatch.setattr(
        server_module, "revision_changes_from_review", controlled_changes
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_preview_handler(queue, article.article_id, items)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    result = {}
    try:

        def revise():
            try:
                _request(
                    base,
                    "/api/revisions",
                    "POST",
                    {"revision": saved_review["revision"]},
                )
            except HTTPError as error:
                result["revision"] = error.code

        revision_thread = threading.Thread(target=revise)
        revision_thread.start()
        assert generation_entered.wait(3)
        assert _request(base, "/api/checklist", "POST", {"checked": [False]})[0] == 200
        release_generation.set()
        revision_thread.join(timeout=3)

        final = queue.get(article.article_id)
        assert result["revision"] == 409
        assert final.state == ArticleState.DRAFT
        assert final.review_checked == [False]
        assert final.screens[0].text == ["旧正文"]
        assert not list(store.revisions_dir.glob("*.json"))
    finally:
        release_generation.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
