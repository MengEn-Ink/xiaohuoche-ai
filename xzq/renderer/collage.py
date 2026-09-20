"""分屏脚本 → 「历史推文风格」拼贴长图 HTML。

版式来自 docs/ribao-html/0908 标杆：荧光描边大字、硬阴影、厚黑分隔线、照片/截图叠贴。
每屏按 role 选一种积木；素材筐里的截图/照片按顺序分配，每张只用一次。
人物避让不做检测：所有图片声明 data-overlay="forbid"（文字不压图），保守但不会错。
"""

from __future__ import annotations

import hashlib
import html
import re
import shutil
from pathlib import Path

from ..models import Article, Material
from .stickers import StickerLib

_CSS = (Path(__file__).parent / "templates" / "collage.css").read_text(encoding="utf-8")

# 大字贴纸词：出现即渲染成黄底黑边小贴纸
STAMPS = ("谴责！", "谴责", "出生模式", "打住", "头条")

ROLE_LABEL = {
    "cover": "🚂 封面",
    "headline": "📢 重点消息",
    "preview": "🚌 预告",
    "race": "🔥 战报",
    "earlybird": "🐦 早鸟",
    "gossip": "🍉 吃瓜",
    "feeling": "❤️ 情感",
    "review": "📣 锐评",
    "shirt": "👕 新衣",
    "item": "📰 速报",
    "checklist": "📋 清单",
    "end": "🚲 结尾",
}


def _esc(s: str) -> str:
    out = html.escape(s)
    for w in STAMPS:
        if w in out:
            out = out.replace(w, f'<span class="badge-inline">{w}</span>', 1)
            break
    return out


def _lines(texts: list[str]) -> list[str]:
    return [t for t in texts if t and not t.startswith("[离线")]


_GENERIC = {"图片", "截图", "照片", "合影", "群聊", "素材", "表情包", "原图", "人物"}


def _stem_tokens(stem: str) -> set[str]:
    """文件名拆成可匹配的词：英文/数字串、中文连续段及其两字片段；去掉「图片」这类泛词。"""
    toks = set(re.findall(r"[A-Za-z0-9]{2,}", stem))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", stem):
        toks.add(run)
        toks.update(run[i : i + 2] for i in range(len(run) - 1))
    return {x for x in toks if x not in _GENERIC}


class _Pool:
    """素材筐图片池：截图优先给吃瓜/重点消息，照片优先给封面/战报；每张只发一次。"""

    def __init__(self, materials: list[Material], asset_dir: Path):
        # 编号 = prompt 里 writer 看到的「素材N」，按 article.materials 顺序、跳过 source_error
        self.by_idx: dict[int, Material] = {}
        n = 0
        for m in materials:
            if m.kind == "source_error":
                continue
            n += 1
            if m.kind in ("photo", "screenshot") and m.source:
                self.by_idx[n] = m
        self.shots = [m for m in materials if m.kind == "screenshot" and m.source]
        self.photos = [m for m in materials if m.kind == "photo" and m.source]
        self.asset_dir = asset_dir
        self.used_photos: list[str] = []
        self.kinds_by_rel: dict[str, str] = {}

    def _take(self, bucket: list[Material]) -> str | None:
        while bucket:
            m = bucket.pop(0)
            src = Path(m.source)
            if not src.is_file():
                # 素材快照可能含已删除文件，继续尝试后续候选。
                continue
            self.asset_dir.mkdir(parents=True, exist_ok=True)
            identity = hashlib.sha256(str(src.resolve()).encode("utf-8")).hexdigest()[
                :10
            ]
            dst = self.asset_dir / f"{identity}-{src.name}"
            if not dst.exists():
                shutil.copy(src, dst)
            rel = f"./{self.asset_dir.name}/{dst.name}"
            self.kinds_by_rel[rel] = m.kind
            if m.kind == "photo":
                self.used_photos.append(rel)
            return rel
        return None

    def is_screenshot(self, rel: str | None) -> bool:
        return bool(rel) and self.kinds_by_rel.get(str(rel)) == "screenshot"

    def named(self, visual: str, exact_only: bool = False) -> str | None:
        """writer 在 visual 里点名了素材就用它：先认完整文件名，再认文件名里的词
        （visual 写「宽哥」也能配到「宽哥点评.webp」）。"""
        if not visual:
            return None
        # 0) 按编号：「用素材3」「素材 3」「#3」——和文件名、顺序无关，最可靠
        for num in re.findall(r"(?:素材|图|#)\s*(\d{1,2})", visual):
            m = self.by_idx.get(int(num))
            if m and (m in self.shots or m in self.photos):
                (self.shots if m in self.shots else self.photos).remove(m)
                rel = self._take([m])
                if rel:
                    return rel
        quotes = [q for q in re.findall(r"[「\"]([^」\"]{4,})[」\"]", visual)]
        for mode in ("exact",) if exact_only else ("exact", "quote", "token"):
            for bucket in (self.shots, self.photos):
                for m in list(bucket):
                    stem = Path(m.source).stem
                    if mode == "exact":
                        hit = bool(stem) and stem in visual
                    elif mode == "quote":  # visual 引了截图里的原话 → 就是这张
                        hit = any(q in (m.text or "") for q in quotes)
                    else:
                        hit = any(tok in visual for tok in _stem_tokens(stem))
                    if hit:
                        bucket.remove(m)
                        rel = self._take([m])
                        if rel:
                            return rel
        return None

    def named_all(self, visual: str, limit: int = 2) -> list[str]:
        """「用素材3和素材5」→ 两张都给；按编号点名的才算多张，文件名模糊匹配只给一张。"""
        out: list[str] = []
        nums = re.findall(r"(?:素材|图|#)\s*(\d{1,2})", visual or "")
        if len(nums) > 1:
            for num in nums[:limit]:
                m = self.by_idx.get(int(num))
                if m and (m in self.shots or m in self.photos):
                    (self.shots if m in self.shots else self.photos).remove(m)
                    rel = self._take([m])
                    if rel:
                        out.append(rel)
            return out
        rel = self.named(visual)
        return [rel] if rel else []

    def screenshot(self, visual: str = "") -> str | None:
        return self.named(visual) or self._take(self.shots) or self._take(self.photos)

    def photo(self, visual: str = "") -> str | None:
        return self.named(visual) or self._take(self.photos) or self._take(self.shots)

    def cover_photo(self) -> str | None:
        """封面最后领图，筐空了就复用第一张发出去的照片：封面没图比重复一张更难看。"""
        return self.photo() or (self.used_photos[0] if self.used_photos else None)


def _img(src: str, alt: str, subject: str = "people") -> str:
    return f'<img src="{src}" alt="{html.escape(alt)}" data-subject="{subject}" data-subject-zone="0,0,100,100" />'


def _stickers_html(paths: list[Path], asset_dir: Path) -> str:
    """贴纸贴在屏右上角，交错旋转；最多两张。"""
    out = []
    for i, p in enumerate(paths[:2]):
        asset_dir.mkdir(parents=True, exist_ok=True)
        dst = asset_dir / p.name
        if not dst.exists():
            shutil.copy(p, dst)
        rot = -8 if i == 0 else 6
        out.append(
            f'<img class="sticker" src="./{asset_dir.name}/{p.name}" alt="贴纸" data-subject="none" '
            f'data-subject-zone="0,0,100,100" style="transform:rotate({rot}deg);right:{18 + i * 150}px" />'
        )
    return "".join(out)


def _hero(
    article: Article, lines: list[str], pool: _Pool, img: str | None = None
) -> str:
    title = _esc(lines[0]) if lines else "小火车日报"
    kicker = " / ".join(_esc(t) for t in lines[1:3])
    img = img or pool.cover_photo()
    fig = (
        f'<figure class="hero-cutout" data-layout="subject-safe" data-overlay="forbid">{_img(img, "封面图")}'
        f'<figcaption class="hero-note">辛庄桥小火车 🚂</figcaption></figure>'
        if img
        else ""
    )
    return (
        '<section class="hero" data-density="compact"><div>'
        f'<div class="topline"><span class="date">{html.escape(article.date)} · {ROLE_LABEL.get(article.kind, "日报")}</span>'
        f'<span class="issue">XZQ / {html.escape(article.article_id)}</span></div>'
        f"<h1>{title}</h1>"
        + (f'<p class="kicker"><mark>{kicker}</mark></p>' if kicker else "")
        + f"</div>{fig}</section>"
    )


def _statement(no: int, role: str, lines: list[str], stickers: str = "") -> str:
    h2 = _esc(lines[0]) if lines else ""
    body = "<br />".join(_esc(t) for t in lines[1:]) or "&nbsp;"
    return (
        f'<section class="story" data-density="compact" data-no="{no:02d}">'
        f'<span class="tag">{no:02d} · {ROLE_LABEL.get(role, role)}</span>'
        f"<h2>{h2}</h2>"
        f'<article class="panel statement-card"><p class="copy">{body}</p></article>{stickers}</section>'
    )


def _split(no: int, role: str, lines: list[str], img: str, stickers: str = "") -> str:
    h2 = _esc(lines[0]) if lines else ""
    body = "<br />".join(_esc(t) for t in lines[1:]) or "&nbsp;"
    return (
        f'<section class="story gossip" data-density="compact" data-no="{no:02d}">'
        f'<span class="tag">{no:02d} · {ROLE_LABEL.get(role, role)}</span>'
        f"<h2>{h2}</h2>"
        f'<div class="split" data-layout="subject-safe" data-overlay="forbid">'
        f'<figure class="photo">{_img(img, "群聊截图", "none")}</figure>'
        f'<article class="panel"><p class="copy">{body}</p></article></div>{stickers}</section>'
    )


def _feature(no: int, role: str, lines: list[str], img: str, stickers: str = "") -> str:
    strong = _esc(lines[0]) if lines else ""
    span = _esc(lines[1]) if len(lines) > 1 else ROLE_LABEL.get(role, role)
    rest = "<br />".join(_esc(t) for t in lines[2:])
    tail = (
        f'<article class="panel"><p class="copy">{rest}</p></article>' if rest else ""
    )
    return (
        f'<section class="story" data-density="compact" data-no="{no:02d}">'
        f'<span class="tag">{no:02d} · {ROLE_LABEL.get(role, role)}</span>'
        f'<figure class="feature-card" data-layout="subject-safe" data-overlay="forbid">{_img(img, "战报配图")}'
        f'<figcaption class="feature-copy"><span>{span}</span><strong>{strong}</strong></figcaption></figure>{tail}{stickers}</section>'
    )


def _finale(lines: list[str]) -> str:
    body = "<br />".join(_esc(t) for t in lines) or "今天就到这里，小编下班"
    return f'<section class="finale" data-density="compact"><p class="copy">{body}</p></section>'


def render_collage_html(article: Article, asset_dir: Path) -> str:
    pool = _Pool(article.materials, asset_dir)
    lib = StickerLib(
        Path(asset_dir).parent.parent / "style"
        if (Path(asset_dir).parent.parent / "style").is_dir()
        else "style"
    )
    # 第一遍：writer 点名的图先预留，避免被前面的屏按顺序拿走
    reserved: dict[int, str] = {}
    for idx, s in enumerate(article.screens):
        if s.role == "end":
            continue
        img = pool.named(s.visual, exact_only=(s.role == "cover"))
        if img:
            reserved[idx] = img
    parts: list[str] = []
    no = 0
    screens = list(article.screens)
    cover_lines = [article.title or "小火车日报"]
    cover_img = None
    for idx, s in enumerate(screens):
        lines = _lines(s.text)
        if s.role == "cover":
            cover_lines, cover_img = lines, reserved.get(idx)
            continue
        if s.role == "end":
            parts.append(_finale(lines))
            continue
        no += 1
        img = reserved.get(idx)
        if not img and s.role in ("gossip", "headline"):
            img = pool.screenshot()
        elif not img and s.role in ("race", "earlybird", "feeling"):
            img = pool.photo()
        stk = _stickers_html(lib.from_visual(s.visual), asset_dir)
        if not img:
            parts.append(_statement(no, s.role, lines, stk))
        elif s.role in ("gossip", "headline"):
            parts.append(_split(no, s.role, lines, img, stk))
        else:
            parts.append(_feature(no, s.role, lines, img, stk))
    # 封面最不挑图，最后领剩下的，别抢内容屏点名的那张
    parts.insert(0, _hero(article, cover_lines, pool, cover_img))
    body = "\n".join(parts)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" />
<title>{html.escape(article.title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700;900&family=ZCOOL+KuaiLe&display=swap" rel="stylesheet" />
<style>{_CSS}
/* ---- 静态渲染覆盖：0908 的 .panel 靠 JS 滚动淡入，截图没有 JS，必须强制可见 ---- */
.panel {{ opacity: 1 !important; transform: none !important; }}
.progress, .top-btn, .lightbox {{ display: none !important; }}
/* 吃瓜屏：截图完整展示不裁切；文字卡放图下面，不压截图 */
.gossip .photo {{ height: auto; max-height: 960px; }}
.gossip .photo img {{ height: auto; max-height: 960px; object-fit: contain; object-position: center; transform: none; }}
.gossip .panel {{ position: static; margin-top: 20px; color: var(--ink); background: var(--paper); }}
.sticker {{ position:absolute; top:70px; z-index:5; max-width:270px; max-height:270px; filter:drop-shadow(6px 7px 0 rgba(5,5,5,.55)); pointer-events:none; }}
.badge-inline {{ display:inline-block; padding:0 .3em; background:var(--lemon); border:3px solid var(--ink); transform:rotate(-3deg); }}
.tag {{ font-size:22px; }}
.finale .copy {{ color: var(--lemon); font-size: clamp(34px,4.5vw,52px); }}
</style></head><body><main class="page">
{body}
<footer class="signature-bar" data-platform-safe-zone="wechat-watermark-right-bottom">XZQ · 辛庄桥小火车</footer>
</main></body></html>"""
