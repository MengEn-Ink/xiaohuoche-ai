from __future__ import annotations

from xzq.models import Article, Screen
from xzq.renderer.to_longimage import article_content_hash, render_html, render_style_html


def test_generated_html_includes_review_ui_by_default(tmp_path):
    article = Article("default-review", "ribao", "0908", screens=[Screen("cover", ["A"])])
    rendered = render_style_html(article, "card", tmp_path)
    assert 'id="reviewShell"' in rendered
    assert 'data-export-ignore="review-ui"' in rendered


def test_review_ui_is_modular_and_anchors_are_stable():
    article = Article("0908-ribao", "ribao", "0908", title="日报", screens=[Screen("cover", ["A"]), Screen("end", ["B"])])
    html = render_html(article, include_review=True)
    assert 'data-review-anchor="screen-00"' in html
    assert 'data-review-anchor="screen-01"' in html
    assert 'data-review-anchor="screen-02"' in html
    assert 'data-export-ignore="review-ui"' in html
    assert "const apiPath = path => `${config.apiBase || ''}${path}`" in html
    assert "new EventSource(apiPath('/events'))" in html
    assert "http-equiv=\"refresh\"" not in html


def test_content_hash_is_stable_but_changes_with_content():
    article = Article("0908-ribao", "ribao", "0908", screens=[Screen("cover", ["A"])])
    first = article_content_hash(article)
    article.updated_at += 100
    assert article_content_hash(article) == first
    article.screens[0].text = ["B"]
    assert article_content_hash(article) != first


def test_export_css_hides_every_review_element():
    html = render_html(Article("x", "ribao", "0908", screens=[Screen("cover", ["A"])]), include_review=True)
    assert "body.exporting [data-export-ignore]{display:none!important}" in html
    assert "page.evaluate(\"document.body.classList.add('exporting')\")" not in html


def test_template_values_are_not_reinterpreted_as_placeholders():
    article = Article(
        "safe", "ribao", "0908", title="标题 {{REVIEW_SCRIPT}}",
        screens=[Screen("cover", ["正文 {{REVIEW_BODY}} 与 {{PAGE_CSS}}"])]
    )
    rendered = render_html(article, include_review=True)
    assert "标题 {{REVIEW_SCRIPT}}" in rendered
    assert "正文 {{REVIEW_BODY}} 与 {{PAGE_CSS}}" in rendered


def test_mobile_review_controls_are_export_safe_and_accessible():
    html = render_html(Article("mobile", "ribao", "0908", screens=[Screen("cover", ["A"])]), include_review=True)
    for element_id in (
        "reviewTopbar", "reviewShell", "reviewBottomBar", "mobileAnnotate",
        "mobileReviewToggle", "mobileChecklist", "mobileSubmit", "approvalDialog",
    ):
        assert f'id="{element_id}"' in html
    assert 'aria-label="移动审阅操作"' in html
    assert 'aria-modal="true"' in html
    assert "env(safe-area-inset-bottom)" in html
    assert "--keyboard-offset" in html
    assert "orientation:landscape" in html
    assert "width:65vw" in html and "width:35vw" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "min-height:44px" in html
    assert "data-export-ignore=\"review-ui\"" in html
