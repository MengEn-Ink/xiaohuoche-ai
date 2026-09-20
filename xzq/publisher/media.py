"""微信素材上传：正文图（uploadimg）与永久封面素材（add_material）。"""
from __future__ import annotations

from pathlib import Path

import requests

from .token import API_BASE, TokenManager


def upload_content_image(token_mgr: TokenManager, image_path: str | Path) -> str:
    """正文内联图：上传后返回微信图床 URL，塞进 <img src>。"""
    url = f"{API_BASE}/media/uploadimg"
    with open(image_path, "rb") as f:
        resp = requests.post(
            url,
            params={"access_token": token_mgr.get_token()},
            files={"media": f},
            timeout=60,
        )
    body = resp.json()
    if "url" not in body:
        raise RuntimeError(f"正文图上传失败：{body}")
    return body["url"]


def upload_cover(token_mgr: TokenManager, cover_path: str | Path) -> str:
    """永久图片素材作为封面，返回 thumb_media_id（草稿 articles.thumb_media_id）。"""
    url = f"{API_BASE}/material/add_material"
    with open(cover_path, "rb") as f:
        resp = requests.post(
            url,
            params={"access_token": token_mgr.get_token(), "type": "image"},
            files={"media": f},
            timeout=60,
        )
    body = resp.json()
    if "media_id" not in body:
        raise RuntimeError(f"封面素材上传失败：{body}")
    return body["media_id"]
