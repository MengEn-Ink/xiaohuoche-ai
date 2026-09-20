from __future__ import annotations

import pytest

from xzq.models import Article, ArticleState, Screen
from xzq.renderer.to_longimage import render_html, render_long_image


def _article() -> Article:
    return Article(
        article_id="0914-ribao",
        kind="ribao",
        date="0914",
        title="0914，小火车日报｜今天只说一件事",
        screens=[
            Screen(role="cover", text=["小火车日报", "干就完了"]),
            Screen(role="gossip", text=["群聊热闻", "一句话说完，不拆碎。"]),
            Screen(role="end", text=["周日发车", "后援车坐满了。"]),
        ],
    )


def test_renderer_uses_the_published_benchmark_visual_contract() -> None:
    output = render_html(_article())

    assert "width:1080px" in output
    assert "--sky:#00a8ff" in output
    assert "--coral:#ff2b00" in output
    assert "--lemon:#fff200" in output
    assert "--lime:#39ff14" in output
    assert "ZCOOL KuaiLe" in output
    assert output.count('data-density="compact"') == 4
    assert 'data-copy-flow="horizontal"' in output
    assert 'data-platform-safe-zone="wechat-watermark-right-bottom"' in output
    assert "XZQ WEEKLY RIDE" in output
    assert "公众号 · 辛庄桥小火车" not in output
    assert "XZQ · 辛庄桥小火车" in output


def test_renderer_keeps_copy_compact_and_escapes_user_content() -> None:
    article = _article()
    article.screens[1].text = ["<群聊热闻>", "A & B"]

    output = render_html(article)

    assert "&lt;群聊热闻&gt;" in output
    assert "A &amp; B" in output
    assert "border-radius:30px" not in output
    assert "（接入 LLM 后此处为 AI 生成文案）" not in output


def test_renderer_marks_offline_placeholder_as_non_publishable() -> None:
    article = _article()
    article.screens = [
        Screen(
            role="cover",
            text=["[离线占位：接入 LLM 后由 AI 按风格生成]"],
            data={"_offline": True},
        )
    ]

    output = render_html(article)

    assert 'data-offline-draft="true"' in output
    assert "离线草稿 · 禁止发布" in output


def test_publish_renderer_rejects_offline_placeholder(tmp_path) -> None:
    article = _article()
    article.state = ArticleState.APPROVED
    article.screens[0].data["_offline"] = True

    with pytest.raises(RuntimeError, match="离线占位稿不能渲染发布产物"):
        render_long_image(article, tmp_path)


def test_publish_renderer_runs_subject_probe_before_opening_browser(tmp_path, monkeypatch) -> None:
    article = _article()
    article.state = ArticleState.APPROVED
    called = []

    def reject(html_path):
        called.append(html_path)
        raise RuntimeError("人物主体被遮挡")

    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_content_density", lambda path: None
    )
    monkeypatch.setattr("xzq.renderer.to_longimage.probe_subject_occlusion", reject, raising=False)
    with pytest.raises(RuntimeError, match="人物主体被遮挡"):
        render_long_image(article, tmp_path)

    assert called == [tmp_path / "0914-ribao.html"]
    assert not (tmp_path / "0914-ribao.png").exists()


def test_publish_renderer_runs_copy_probe_before_export(tmp_path, monkeypatch) -> None:
    article = _article()
    article.state = ArticleState.APPROVED
    called = []

    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_content_density", lambda path: None
    )
    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_subject_occlusion", lambda path: None
    )

    def reject(html_path):
        called.append(html_path)
        raise RuntimeError("贴纸遮挡大字")

    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_copy_occlusion", reject, raising=False
    )
    with pytest.raises(RuntimeError, match="贴纸遮挡大字"):
        render_long_image(article, tmp_path)

    assert called == [tmp_path / "0914-ribao.html"]
    assert not (tmp_path / "0914-ribao.png").exists()


def test_publish_renderer_runs_image_identity_gate_before_export(tmp_path, monkeypatch) -> None:
    article = _article()
    article.state = ArticleState.APPROVED
    called = []

    def reject(paths):
        called.append(list(paths))
        raise RuntimeError("同源图片重复")

    monkeypatch.setattr(
        "xzq.renderer.to_longimage.validate_image_identities", reject, raising=False
    )
    with pytest.raises(RuntimeError, match="同源图片重复"):
        render_long_image(article, tmp_path)

    assert called == [[tmp_path / "0914-ribao.html"]]
    assert not (tmp_path / "0914-ribao.png").exists()


def test_publish_renderer_runs_density_gate_before_export(tmp_path, monkeypatch) -> None:
    article = _article()
    article.state = ArticleState.APPROVED
    called = []

    def reject(path):
        called.append(path)
        raise RuntimeError("信息填充率不足")

    monkeypatch.setattr(
        "xzq.renderer.to_longimage.validate_image_identities", lambda paths: None
    )
    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_subject_occlusion", lambda path: None
    )
    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_copy_occlusion", lambda path: None
    )
    monkeypatch.setattr(
        "xzq.renderer.to_longimage.probe_content_density", reject, raising=False
    )
    with pytest.raises(RuntimeError, match="信息填充率不足"):
        render_long_image(article, tmp_path)

    assert called == [tmp_path / "0914-ribao.html"]
    assert not (tmp_path / "0914-ribao.png").exists()


def test_style_changes_export_hash_and_card_supports_all_roles():
    from xzq.models import SCREEN_ROLES
    from xzq.renderer.to_longimage import ROLE_STYLE, article_content_hash

    article = _article()
    assert article_content_hash(article, "raw") != article_content_hash(article, "0908")
    assert SCREEN_ROLES <= ROLE_STYLE.keys()
