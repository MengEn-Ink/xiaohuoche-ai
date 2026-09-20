"""Article -> shared HTML body template -> 1080px PNG."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from ..models import Article, ArticleState
from ..reviewer.revision import content_hash
from ..subject_probe import probe_subject_occlusion
from ..copy_probe import probe_copy_occlusion
from ..image_identity import validate_image_identities
from ..density_probe import probe_content_density
from .review_ui import review_fragments

ROLE_STYLE = {
    "cover": {
        "label": "封面",
        "emoji": "🚂",
        "bg": "var(--sky)",
        "accent": "var(--lemon)",
    },
    "headline": {
        "label": "重点消息",
        "emoji": "📢",
        "bg": "var(--lemon)",
        "accent": "var(--coral)",
    },
    "preview": {
        "label": "下期预告",
        "emoji": "🚌",
        "bg": "var(--lime)",
        "accent": "var(--coral)",
    },
    "race": {
        "label": "骑行战报",
        "emoji": "🔥",
        "bg": "var(--lime)",
        "accent": "var(--coral)",
    },
    "earlybird": {
        "label": "早鸟组",
        "emoji": "🐦",
        "bg": "var(--sky)",
        "accent": "var(--lemon)",
    },
    "gossip": {
        "label": "群聊热闻",
        "emoji": "🍉",
        "bg": "var(--coral)",
        "accent": "var(--lemon)",
    },
    "feeling": {
        "label": "今日有感",
        "emoji": "⚡",
        "bg": "var(--lemon)",
        "accent": "var(--coral)",
    },
    "review": {
        "label": "宽哥点评",
        "emoji": "📣",
        "bg": "var(--sky)",
        "accent": "var(--lemon)",
    },
    "shirt": {
        "label": "新衣发布",
        "emoji": "👕",
        "bg": "var(--lemon)",
        "accent": "var(--coral)",
    },
    "item": {
        "label": "速报",
        "emoji": "📰",
        "bg": "var(--sky)",
        "accent": "var(--lemon)",
    },
    "checklist": {
        "label": "出发清单",
        "emoji": "📋",
        "bg": "var(--lime)",
        "accent": "var(--coral)",
    },
    "end": {
        "label": "收车",
        "emoji": "🚲",
        "bg": "var(--ink)",
        "accent": "var(--lemon)",
    },
}
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "style" / "templates" / "ribao"
RENDERER_DIR = Path(__file__).resolve().parent
SUPPORTED_STYLES = {"raw", "0908", "card"}


def template_version(style: str = "card") -> str:
    """返回指定版式实际代码和模板的指纹，切换 style 或改 CSS 都会使旧导出失效。"""
    if style not in SUPPORTED_STYLES:
        raise ValueError(f"不支持的 renderer.style：{style}")
    paths = {
        "card": (
            TEMPLATE_DIR / "page.html",
            TEMPLATE_DIR / "page.css",
            Path(__file__),
        ),
        "raw": (
            Path(__file__),
            RENDERER_DIR / "raw.py",
            RENDERER_DIR / "templates" / "raw.css",
            RENDERER_DIR / "collage.py",
            RENDERER_DIR / "cutout.py",
            RENDERER_DIR / "faces.py",
            RENDERER_DIR / "stickers.py",
        ),
        "0908": (
            Path(__file__),
            RENDERER_DIR / "collage.py",
            RENDERER_DIR / "templates" / "collage.css",
            RENDERER_DIR / "stickers.py",
        ),
    }[style]
    digest = hashlib.sha256(style.encode("utf-8"))
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def article_content_hash(article: Article, style: str = "card") -> str:
    return content_hash(article, template_version(style))


def _material_gallery(article: Article) -> str:
    images: list[str] = []
    for material in article.materials:
        if material.kind not in {"photo", "screenshot"} or not material.source:
            continue
        source = Path(material.source)
        if not source.is_absolute() or not source.is_file():
            continue
        label = str(material.meta.get("name") or material.text or source.name)[:120]
        images.append(
            f'<figure class="material-image"><img src="{html.escape(source.resolve().as_uri())}" '
            f'alt="{html.escape(label)}"><figcaption>{html.escape(material.text or label)}</figcaption></figure>'
        )
    return f'<section class="material-gallery">{"".join(images)}</section>' if images else ""


def _sections(article: Article) -> str:
    output: list[str] = []
    for index, screen in enumerate(article.screens, 1):
        style = ROLE_STYLE.get(screen.role, ROLE_STYLE["end"])
        paragraphs = (
            "".join(
                f'<p data-copy-flow="horizontal">{_decorate(text)}</p>'
                for text in screen.text
                if text and not text.startswith("[离线")
            )
            or '<p class="placeholder" data-copy-flow="horizontal">等待真实素材与人工文案</p>'
        )
        visual = ""
        if screen.visual and not screen.visual.startswith("[待"):
            visual = f'<div class="material-note" data-copy-flow="horizontal">配图提示：{html.escape(screen.visual)}</div>'
        label = next(
            (text for text in screen.text if text and not text.startswith("[离线")),
            style["label"],
        )
        output.append(
            f'<section class="screen role-{html.escape(screen.role)}" data-density="compact" '
            f'data-review-anchor="screen-{index:02d}" data-review-label="{html.escape(label[:80])}">'
            f'<div class="badge">{style["emoji"]} {index:02d} / {style["label"]}</div>'
            f'<div class="copy">{paragraphs}</div>{visual}</section>'
        )
    return "\n".join(output)


def render_html(
    article: Article,
    include_review: bool = False,
    gate_passed: bool = True,
    review_api_base: str = "",
) -> str:
    """预览与导出共用同一模板；审阅模块仅作为可隐藏外壳注入。"""
    template = (TEMPLATE_DIR / "page.html").read_text(encoding="utf-8")
    page_css = (TEMPLATE_DIR / "page.css").read_text(encoding="utf-8")
    offline = any(bool(screen.data.get("_offline")) for screen in article.screens)
    review_head = review_body = review_script = ""
    if include_review:
        review_head, review_body, review_script = review_fragments(
            {
                "articleId": article.article_id,
                "contentHash": article_content_hash(article),
                "gatePassed": gate_passed,
                "apiBase": review_api_base.rstrip("/"),
            }
        )
    values = {
        "TITLE": html.escape(article.title or _cover_title(article)),
        "PAGE_CSS": page_css,
        "REVIEW_HEAD": review_head,
        "BODY_ATTRS": ' data-offline-draft="true"' if offline else "",
        "REVIEW_BODY": review_body,
        "DRAFT_BANNER": '<div class="draft-banner">离线草稿 · 禁止发布</div>'
        if offline
        else "",
        "DATE": html.escape(article.date),
        "COVER_TITLE": html.escape(_cover_title(article)),
        "MATERIALS": _material_gallery(article),
        "SECTIONS": _sections(article),
        "REVIEW_SCRIPT": review_script,
    }
    return re.sub(
        r"\{\{([A-Z_]+)\}\}",
        lambda match: values.get(match.group(1), match.group(0)),
        template,
    )


def _with_review_shell(
    page: str, article: Article, style: str, gate_passed: bool
) -> str:
    """给 raw/collage 预览补审阅外壳，并为每屏补稳定锚点。"""
    review_head, review_body, review_script = review_fragments(
        {
            "articleId": article.article_id,
            "contentHash": article_content_hash(article, style),
            "gatePassed": gate_passed,
        }
    )
    counter = iter(range(1, len(article.screens) + 1))

    def anchor(match: re.Match[str]) -> str:
        index = next(counter, 0)
        return f'<section data-review-anchor="screen-{index:02d}" data-review-label="第 {index} 屏"{match.group(1)}'

    page = re.sub(r"<section([^>]*)", anchor, page)
    page = page.replace("</head>", f"{review_head}</head>", 1)
    page = page.replace("<body>", f"<body>{review_body}", 1)
    return page.replace("</body>", f"{review_script}</body>", 1)


def render_style_html(
    article: Article,
    style: str,
    asset_dir: str | Path,
    include_review: bool = True,
    gate_passed: bool = True,
) -> str:
    """预览与导出共用同一个 style 分派，避免所见版式与最终 PNG 不一致。"""
    if style == "card":
        return render_html(
            article, include_review=include_review, gate_passed=gate_passed
        )
    if style == "0908":
        from .collage import render_collage_html

        page = render_collage_html(article, Path(asset_dir))
    elif style == "raw":
        from .raw import render_raw_html

        page = render_raw_html(article, Path(asset_dir))
    else:
        raise ValueError(f"不支持的 renderer.style：{style}")
    return (
        _with_review_shell(page, article, style, gate_passed)
        if include_review
        else page
    )


def _cover_title(article: Article) -> str:
    if article.title:
        return article.title
    return {"ribao": "小火车日报", "wanbao": "小火车晚报", "tuanjian": "团建必读"}.get(
        article.kind, "小火车日报"
    )


def _decorate(text: str) -> str:
    output = html.escape(text)
    for word in ("谴责！", "谴责", "爆金币", "干就完了"):
        if word in output:
            output = output.replace(word, f'<span class="stamp">{word}</span>', 1)
            break
    return output


def render_long_image(article: Article, out_dir: str | Path, style: str = "raw") -> str:
    """style：raw = 2024 年真日报版式（默认）；0908 = 拼贴卡片版；card = 模板 page.html（带审阅锚点）。"""
    if article.state not in (
        ArticleState.APPROVED,
        ArticleState.IMAGE,
        ArticleState.PUSHED,
    ):
        raise RuntimeError(f"状态 {article.state.value} 未审核通过，不能渲染长图")
    if any(bool(screen.data.get("_offline")) for screen in article.screens):
        raise RuntimeError("离线占位稿不能渲染发布产物，请先补齐真实文案并重新审核")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "未安装 playwright，请先 pip install playwright && playwright install chromium"
        ) from error

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / f"{article.article_id}.html"
    png_path = out / f"{article.article_id}.png"
    cover_path = out / f"{article.article_id}-cover.jpg"
    asset_dir = out / f"{article.article_id}-assets"
    html_path.write_text(
        render_style_html(article, style, asset_dir),
        encoding="utf-8",
    )
    if style == "raw":
        validate_image_identities([html_path])
        probe_content_density(html_path)
        probe_subject_occlusion(html_path)
        probe_copy_occlusion(html_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1080, "height": 1200}, device_scale_factor=1
        )
        page.goto(html_path.resolve().as_uri())
        page.evaluate("document.body.classList.add('exporting')")
        page.wait_for_timeout(
            300 if style == "card" else 1200
        )  # 版式图多、要等字体图片和抠图
        page.screenshot(path=str(png_path), full_page=True)
        page.add_style_tag(
            content=(
                "section.scr.cover{height:460px!important;min-height:460px!important;}"
                "section.scr.cover .title{font-size:150px!important;}"
                "section.scr.cover .date{font-size:72px!important;}"
                "section.scr.cover .kick{left:300px!important;top:170px!important;"
                "width:750px!important;}"
                "section.scr.cover .kick .bl{font-size:76px!important;line-height:1.08!important;}"
                "section.scr.cover>.stk{right:2px!important;top:322px!important;"
                "max-width:112px!important;max-height:112px!important;}"
            )
        )
        cover_png = page.locator("section.scr.cover").first.screenshot()
        browser.close()

    from PIL import Image

    import io

    with Image.open(io.BytesIO(cover_png)) as cover:
        cover.convert("RGB").save(cover_path, format="JPEG", quality=92)
    article.image_path = str(png_path)
    article.cover_path = str(cover_path)
    if article.state == ArticleState.APPROVED:
        article.transition(ArticleState.IMAGE)
    article.render_style = style
    article.content_hash = article_content_hash(article, style)
    article.export_hash = article.content_hash
    return str(png_path)
