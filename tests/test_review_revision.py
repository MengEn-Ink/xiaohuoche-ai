from __future__ import annotations

import json

import pytest

from xzq.models import Article, ArticleState, Screen
from xzq.publisher.draft import push_draft
from xzq.renderer.to_longimage import render_long_image
from xzq.reviewer.revision import (
    ReviewStore,
    create_revision,
    empty_review,
    restore_revision,
    validate_revision_changes,
)


def test_review_is_atomic_and_rejects_invalid_schema(tmp_path):
    store = ReviewStore(tmp_path, "a")
    review = empty_review("a")
    review["generalNote"] = "整页改短"
    saved = store.save(review)
    assert saved["revision"] == 1
    assert not list(store.root.glob("*.tmp"))
    invalid = dict(saved); invalid["annotations"] = [{"id": "x"}]
    with pytest.raises(ValueError):
        store.save(invalid)


def test_revision_preserves_previous_article_and_applies_explicit_patch(tmp_path):
    article = Article("a", "ribao", "0908", title="旧标题", state=ArticleState.DRAFT, screens=[Screen("cover", ["旧正文"])])
    store = ReviewStore(tmp_path, "a")
    review = empty_review("a")
    review["annotations"] = [{"id": "1", "anchor": "screen-01", "position": {"x": 1, "y": 2}, "text": "改一下", "status": "open", "createdAt": "x", "updatedAt": "x"}]
    revised, metadata = create_revision(article, store, review, {"title": "新标题", "screens": {"0": {"text": ["新正文"]}}})
    assert article.title == "旧标题" and article.screens[0].text == ["旧正文"]
    assert revised.title == "新标题" and revised.screens[0].text == ["新正文"]
    snapshot = json.loads((store.revisions_dir / f"{metadata['revisionId']}.json").read_text())
    assert snapshot["sourceArticle"]["title"] == "旧标题"
    assert snapshot["requestedChanges"][0]["id"] == "1"


def test_restore_revision_archives_current_before_rollback(tmp_path):
    original = Article("a", "ribao", "0908", title="旧版", state=ArticleState.DRAFT, screens=[Screen("cover", ["旧"])])
    store = ReviewStore(tmp_path, "a")
    review = empty_review("a")
    revised, metadata = create_revision(original, store, review, {"title": "新版"})
    restored = restore_revision(revised, store, metadata["revisionId"])
    assert restored.title == "旧版"
    files = list(store.revisions_dir.glob("*.json"))
    assert len(files) == 2
    assert any("恢复" in path.read_text(encoding="utf-8") for path in files)


def test_server_update_uses_expected_revision_cas(tmp_path):
    store = ReviewStore(tmp_path, "a")
    first = store.save(empty_review("a"))
    stale = dict(first)
    current = dict(first); current["generalNote"] = "浏览器新值"
    store.save(current, expected_revision=1)

    with pytest.raises(RuntimeError, match="其他客户端"):
        store.save_server_update(stale, expected_revision=1)
    assert store.load()["generalNote"] == "浏览器新值"


def test_revision_invalidates_existing_approval_and_export(tmp_path):
    for state in (ArticleState.APPROVED, ArticleState.IMAGE):
        article = Article(
            f"a-{state.value}", "ribao", "0908", state=state,
            screens=[Screen("cover", ["旧正文"])], review_items=["事实准确"],
            review_checked=[True], image_path="old.png", cover_path="old-cover.jpg",
            content_hash="old", export_hash="old",
        )
        revised, _ = create_revision(
            article, ReviewStore(tmp_path, article.article_id), empty_review(article.article_id),
            {"screens": {"0": {"text": ["新正文"]}}},
        )
        assert revised.state == ArticleState.DRAFT
        assert revised.review_checked == []
        assert revised.image_path == revised.cover_path == revised.export_hash == ""


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"title": 1}, "title"),
        ({"title": "x" * 201}, "title"),
        ({"screens": []}, "screens"),
        ({"screens": {"01": {"text": ["x"]}}}, "screen index"),
        ({"screens": {"1": {"text": ["x"]}}}, "越界"),
        ({"screens": {"0": {"text": "x"}}}, "text"),
        ({"screens": {"0": {"text": [1]}}}, "text"),
        ({"screens": {"0": {"text": ["x" * 2_001]}}}, "text"),
        ({"screens": {"0": {"text": ["x"] * 101}}}, "text"),
        ({"screens": {"0": {"visual": 1}}}, "visual"),
        ({"screens": {"0": {"visual": "x" * 4_001}}}, "visual"),
        ({"screens": {"0": {"data": []}}}, "data"),
        ({"screens": {"0": {"data": {"large": "x" * 64_001}}}}, "data"),
        ({"screens": {"0": {"unknown": "x"}}}, "未知字段"),
        ({"unknown": True}, "未知字段"),
    ],
)
def test_revision_patch_schema_rejects_invalid_values_before_snapshot(tmp_path, changes, message):
    article = Article("schema", "ribao", "0908", state=ArticleState.DRAFT, screens=[Screen("cover", ["旧"])])
    store = ReviewStore(tmp_path, article.article_id)
    before = article.to_json()

    with pytest.raises(ValueError, match=message):
        create_revision(article, store, empty_review(article.article_id), changes)

    assert article.to_json() == before
    assert not list(store.revisions_dir.glob("*.json"))


def test_revision_patch_schema_accepts_supported_fields():
    article = Article("schema", "ribao", "0908", screens=[Screen("cover", ["旧"])])
    assert validate_revision_changes(article, {
        "title": "新标题",
        "screens": {"0": {"role": "cover", "text": ["新"], "visual": "图", "data": {"n": 1}}},
    })["screens"]["0"]["data"] == {"n": 1}


def test_llm_revision_uses_shared_patch_validator():
    from xzq.writer.generate import revision_changes_from_review

    class InvalidLLM:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def chat(*_args, **_kwargs):
            return '{"screens":{"0":{"text":"不是数组"}}}'

    article = Article("llm", "ribao", "0908", screens=[Screen("cover", ["旧"])])
    annotations = [{"id": "1", "anchor": "screen-01", "status": "open", "text": "修改"}]
    with pytest.raises(ValueError, match="text"):
        revision_changes_from_review(article, annotations, "", InvalidLLM())


@pytest.mark.parametrize("state", [ArticleState.APPROVED, ArticleState.IMAGE])
def test_revised_approved_or_image_article_cannot_render_or_push(tmp_path, state):
    article = Article(
        f"blocked-{state.value}", "ribao", "0908", state=state,
        screens=[Screen("cover", ["旧"])], review_checked=[True],
    )
    revised, _ = create_revision(
        article, ReviewStore(tmp_path, article.article_id), empty_review(article.article_id),
        {"screens": {"0": {"text": ["新"]}}},
    )
    with pytest.raises(RuntimeError, match="未审核通过"):
        render_long_image(revised, tmp_path)
    with pytest.raises(RuntimeError, match="需先渲染长图"):
        push_draft(revised, object())
