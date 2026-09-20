"""新建草稿 draft/add（个人主体可用）；企业认证号可选 freepublish 群发。

正文用 renderer 生成的 HTML：长图先传图床拿 URL，再包进 <img>。
"""
from __future__ import annotations

import json

import requests

from ..models import Article, ArticleState
from ..renderer.to_longimage import render_html
from . import media
from .token import API_BASE, TokenManager


def _content_html(article: Article, image_url: str | None) -> str:
    """正文：优先长图（图床 URL），无图时回退分屏文字 HTML。"""
    if image_url:
        return f'<p><img src="{image_url}" style="width:100%;"/></p>'
    return render_html(article)


def push_draft(
    article: Article,
    token_mgr: TokenManager,
    author: str = "辛庄桥小火车",
    auto_publish: bool = False,
) -> str:
    """把已渲染长图的稿件推进草稿箱；返回 media_id。

    auto_publish=True（仅企业认证号）会在草稿基础上调 freepublish 群发；
    个人主体必须 False，最后一步手机人工群发。
    """
    if article.state != ArticleState.IMAGE:
        raise RuntimeError(f"状态 {article.state.value}，需先渲染长图(IMAGE)才能推送")
    token = token_mgr.get_token()

    image_url = None
    if article.image_path:
        image_url = media.upload_content_image(token_mgr, article.image_path)

    article_body = {
        "title": article.title,
        "author": author,
        "digest": _digest(article),
        "content": _content_html(article, image_url),
        "content_source_url": "",
        "need_open_comment": 1,
    }
    # 封面使用 renderer 从长图顶部按 2.35:1 自动裁出的 900x383 图片，避免长图被压扁。
    cover_path = article.cover_path or article.image_path
    if cover_path:
        article_body["thumb_media_id"] = media.upload_cover(token_mgr, cover_path)

    resp = requests.post(
        f"{API_BASE}/draft/add",
        params={"access_token": token},
        data=json.dumps({"articles": [article_body]}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=60,
    )
    body = resp.json()
    if "media_id" not in body:
        raise RuntimeError(f"新建草稿失败：{body}")
    article.draft_media_id = body["media_id"]
    article.transition(ArticleState.PUSHED)

    if auto_publish:
        _freepublish(token_mgr, body["media_id"])
    return body["media_id"]


def _freepublish(token_mgr: TokenManager, draft_media_id: str) -> str:
    """企业认证号专属：发布草稿。个人号无权限（errcode 48000 类）。"""
    resp = requests.post(
        f"{API_BASE}/freepublish/submit",
        params={"access_token": token_mgr.get_token()},
        json={"media_id": draft_media_id},
        timeout=30,
    )
    body = resp.json()
    if body.get("errcode") not in (0, None):
        raise RuntimeError(f"群发失败（个人主体无权限属正常）：{body}")
    return body.get("publish_id", "")


def _digest(article: Article) -> str:
    """摘要：取前几屏文字拼一段，截断到 120 字以内。"""
    texts = [t for s in article.screens for t in s.text if t and not t.startswith("[")]
    digest = "；".join(texts)[:120]
    return digest or article.title
