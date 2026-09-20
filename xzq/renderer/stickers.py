"""贴纸库 + 梗图生成缓存。

- 贴纸：`style/stickers/*.png`，清单 `stickers.json`。writer 在 visual 里写「贴纸：熊猫头」或「贴纸：panda_shock」。
- 梗图：writer 写「梗图：喜报|刀刀成为辛庄桥史铁生」，用 meme-generator 生成一张，存到
  `style/stickers/generated/` 并登记进清单；下次同样的模板+文字直接复用不再生成。
  meme-generator 没装或资源没下时静默跳过，不影响出稿。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

STICKER_RE = re.compile(r"贴纸[:：]\s*([^\s，,。；;、（）()「」]+)")
MEME_RE = re.compile(
    r"梗图[:：]\s*([^\s|｜，,。；;]+)\s*[|｜]\s*([^\n]+?)(?=\s*(?:；|;|。|$))"
)

# 梗图模板：中文名 → meme-generator key（只收纯文字、不用人像的）
MEME_TEMPLATES = {
    "喜报": "good_news",
    "悲报": "bad_news",
    "奖状": "certificate",
    "升天": "ascension",
    "高低情商": "high_eq",
    "5000兆": "5000choyen",
    "鲁迅说": "luxun_say",
    "举牌": "bronya_holdsign",
}
_MANIFEST_LOCK = threading.RLock()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class StickerLib:
    def __init__(self, style_dir: str | Path = "style"):
        self.dir = Path(style_dir) / "stickers"
        self.gen_dir = self.dir / "generated"
        self.manifest_path = self.dir / "stickers.json"
        self.items: list[dict] = []
        self._doc = "贴纸库"
        self._reload_manifest()

    def _reload_manifest(self) -> None:
        if self.manifest_path.exists():
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.items = data.get("stickers", [])
            self._doc = data.get("_doc", self._doc)

    def menu(self) -> str:
        """给 writer 看的菜单：key / 中文名 / 用途。"""
        names = "、".join(s["name"] for s in self.items if not s.get("generated"))
        memes = "、".join(MEME_TEMPLATES)
        return f"贴纸（visual 写「贴纸：名字」）：{names}。梗图（visual 写「梗图：模板|文字」）：{memes}。"

    def find(self, name: str) -> Path | None:
        name = name.strip()
        for s in self.items:
            exact = name in (s["key"], s["name"])
            fuzzy = not s.get("generated") and (
                name in s["name"] or name in s.get("use", "")
            )
            if exact or fuzzy:
                p = self.dir / s["file"]
                if p.exists():
                    return p
        # writer 写「熊猫头」「猫」这种半个名字：按两字片段找最像的一张
        grams = {name[i : i + 2] for i in range(len(name) - 1)}
        best, score = None, 0
        for s in self.items:
            if s.get("generated"):
                continue
            hay = s["name"] + s.get("use", "")
            k = sum(1 for g in grams if g in hay)
            if k > score:
                best, score = s, k
        if best and score:
            p = self.dir / best["file"]
            if p.exists():
                return p
        return None

    def unknown(self, visual: str) -> list[str]:
        """visual 里点了但库里找不到的贴纸名（自检用）。"""
        return [
            m.group(1)
            for m in STICKER_RE.finditer(visual or "")
            if not self.find(m.group(1))
        ]

    def from_visual(self, visual: str) -> list[Path]:
        """解析一屏的 visual：贴纸直接找，找不到就拿那几个字生成一张举牌梗图；梗图生成或复用。"""
        out: list[Path] = []
        for m in STICKER_RE.finditer(visual or ""):
            p = self.find(m.group(1)) or self.meme("举牌", m.group(1))
            if p:
                out.append(p)
        for m in MEME_RE.finditer(visual or ""):
            p = self.meme(m.group(1).strip(), m.group(2).strip())
            if p:
                out.append(p)
        return out

    def meme(self, template: str, text: str) -> Path | None:
        key = MEME_TEMPLATES.get(template, template)
        texts = [t.strip() for t in re.split(r"[,，/]", text) if t.strip()]
        digest = hashlib.md5(f"{key}|{'|'.join(texts)}".encode()).hexdigest()[:10]
        path = self.gen_dir / f"{key}-{digest}.png"
        with _MANIFEST_LOCK:
            if path.exists():
                return path
            try:
                import meme_generator as mg
            except ImportError:
                return None
            try:
                meme = mg.get_meme(key)
                p = meme.info.params
                if p.min_images > 0:
                    return None
                n = max(p.min_texts, min(len(texts), p.max_texts)) if p.max_texts else 0
                texts = (texts + [""] * n)[:n]
                data = meme.generate([], texts, {})
                if not isinstance(data, (bytes, bytearray)):
                    return None
            except Exception:
                return None
            _atomic_write(path, bytes(data))
            # 锁内重新读取，避免另一个 StickerLib 实例基于旧清单覆盖刚写入的条目。
            self._reload_manifest()
            if not any(
                item.get("file") == f"generated/{path.name}" for item in self.items
            ):
                self.items.append(
                    {
                        "key": path.stem,
                        "name": f"{template}：{text}",
                        "file": f"generated/{path.name}",
                        "use": "生成过的梗图，同模板同文字直接复用",
                        "generated": True,
                        "template": key,
                        "texts": texts,
                    }
                )
                payload = (
                    json.dumps(
                        {"_doc": self._doc, "stickers": self.items},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                _atomic_write(self.manifest_path, payload.encode("utf-8"))
            return path
