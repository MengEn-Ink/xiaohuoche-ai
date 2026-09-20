"""分屏脚本 → 「2024 年真日报」版式（604/605/528 那套），不是 0908 复刻版。

视觉 DNA：日报以 605 为唯一 ground truth，整页固定天蓝底；没有卡片和栏目标签；大字直接压在底上，
黑描边 + 硬阴影，逐行换色（荧光绿/亮黄/青/粉/白）；照片黑边、歪着贴、互相压；人物抠成透明图
压超大字；群聊截图原样贴；表情包贴纸乱撒；结尾红蓝爱心。

密度对标 604 封面：一屏一张大照片 + 两三张贴纸，字直接压在照片上没脸的那条带子里。
横版照片才压字（不裁图，人脸框坐标才对得上）；竖版照片、群聊截图、检测不到空闲带的照片，
字放照片旁边。
"""

from __future__ import annotations

import hashlib
import html
import re
import shutil
from pathlib import Path

from ..models import Article
from .collage import _Pool, _lines
from .cutout import SubjectAsset, prepare_subject_asset
from .subject_meta import SubjectMetadataError
from .faces import corner_free, faces, free_band
from .stickers import StickerLib

_CSS = (Path(__file__).parent / "templates" / "raw.css").read_text(encoding="utf-8")

# 605 是日报唯一视觉 ground truth：日报固定天蓝底；其他刊型暂保留各自颜色。
BG = {
    "ribao": ["#A6DBF5"],
    "zaobao": ["#C7A5EA", "#7FD9A8"],
    "wanbao": ["linear-gradient(180deg,#1E90FF 0%,#3FE0D0 100%)", "#8E86B8"],
    "kuaixun": ["#0A0A0A"],
    "subao": ["#BFEBD0", "#E8DDC9"],
}
LINE_COLORS = ["#EAFF00", "#FFFFFF", "#25F4EE", "#FF5FB0"]
KIND_TITLE = {
    "ribao": "小火车日报",
    "wanbao": "小火车晚报",
    "zaobao": "小火车早报",
    "kuaixun": "小火车快讯",
    "subao": "辛庄桥小火车速报",
    "tuanjian": "团建必读",
}
# 贴纸落点（屏幕坐标）；文字左对齐，贴纸尽量落右侧和空角
SLOT_CSS = {
    "tl": "left:30px;top:28px",
    "tr": "right:100px;top:28px",
    "bl": "left:30px;bottom:28px",
    "br": "right:100px;bottom:28px",
    "mr": "right:104px;top:38%",
    "ml": "left:36px;top:40%",
    "cr": "right:40px;top:230px",
    "tc": "left:50%;top:22px;margin-left:-110px",
}
MAX_STICKERS = 3
# writer 没点贴纸时按角色补一张：真稿几乎没有光板屏，纯色底一大片就闷了
DEFAULT_STICKER = {
    "gossip": "熊猫头惊",
    "headline": "头条贴",
    "race": "拿枪黄脸",
    "earlybird": "彩鸟",
    "feeling": "放大镜",
    "item": "熊猫头抽烟",
    "checklist": "打住狗",
    "cover": "王咪猫",
    "review": "放大镜",
    "shirt": "小红花",
}


def _bg(article: Article) -> str:
    opts = BG.get(article.kind, BG["ribao"])
    h = int(hashlib.md5(article.article_id.encode()).hexdigest(), 16)
    return opts[h % len(opts)]


def _esc(s: str) -> str:
    return html.escape(s)


def _fit(t: str, width_px: int, base: int) -> int:
    """短句尽量撑满版面；长句宁可自然换行，也不牺牲日报的大字视觉。"""
    units = sum(0.6 if ch.isascii() else 1.0 for ch in t) or 1
    return max(88, min(base, int(width_px / (units * 1.04))))


def _copy_flow(lines: list[str]) -> str:
    """长文案必须横向成行；只有极短口号允许纵向成列。"""
    compact = [re.sub(r"\s+", "", line) for line in lines if line.strip()]
    total = sum(len(line) for line in compact)
    longest = max((len(line) for line in compact), default=0)
    return "vertical" if len(compact) <= 2 and total <= 10 and longest <= 6 else "horizontal"


def _needs_wide_copy(lines: list[str]) -> bool:
    compact = [re.sub(r"\s+", "", line) for line in lines if line.strip()]
    return sum(len(line) for line in compact) >= 15 or any(
        len(line) > 14 for line in compact
    )


def _big(lines: list[str], start: int = 0, size: str = "l", width_px: int = 980) -> str:
    """逐行换色的大字块；长句自然横排换行，交错缩进 + 轻旋转，像手贴的。"""
    base = 96 if size == "l" else 88
    out = []
    for i, t in enumerate(lines):
        c = LINE_COLORS[(start + i) % len(LINE_COLORS)]
        ind = (i % 3) * 28
        rot = (-1.6, 0.8, -0.6, 1.4)[i % 4]
        fs = _fit(t, width_px - ind, base)
        units = sum(0.6 if ch.isascii() else 1.0 for ch in t) or 1
        wrap = (
            "white-space:normal;overflow-wrap:anywhere"
            if width_px - ind < units * 88 * 1.04
            else ""
        )
        out.append(
            f'<div class="bl" style="color:{c};font-size:{fs}px;margin-left:{ind}px;transform:rotate({rot}deg);{wrap}">{_esc(t)}</div>'
        )
    return "".join(out)


def _copy_img(src: str | Path, asset_dir: Path) -> str:
    src = Path(src)
    asset_dir.mkdir(parents=True, exist_ok=True)
    dst = asset_dir / src.name
    if not dst.exists() and src.exists():
        shutil.copy(src, dst)
    return f"./{asset_dir.name}/{src.name}"


def _img_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _has_transparency(path: Path) -> bool:
    """预制透明人物图是已完成的非生成式抠图，应直接复用而不是重新分割。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            return (
                "A" in im.getbands()
                and im.convert("RGBA").getchannel("A").getextrema()[0] < 255
            )
    except Exception:
        return False


def _stickers(paths: list[Path], asset_dir: Path, slots: list[str], seed: int) -> str:
    out = []
    for i, p in enumerate(paths[:MAX_STICKERS]):
        if i >= len(slots):
            break
        rot = (-9, 7, -5, 11)[(seed + i) % 4]
        out.append(
            f'<img class="stk" src="{_copy_img(p, asset_dir)}" alt="贴纸" data-subject="none" data-subject-zone="0,0,100,100" '
            f'style="{SLOT_CSS[slots[i]]};transform:rotate({rot}deg)" />'
        )
    return "".join(out)


def _flow_stickers(paths: list[Path], asset_dir: Path, seed: int) -> str:
    items = []
    for i, path in enumerate(paths[:MAX_STICKERS]):
        rot = (-6, 5, -3)[(seed + i) % 3]
        items.append(
            f'<img class="stk flow" src="{_copy_img(path, asset_dir)}" alt="贴纸" '
            f'data-subject="none" data-subject-zone="0,0,100,100" '
            f'style="transform:rotate({rot}deg)" />'
        )
    return f'<div class="sticker-row">{"".join(items)}</div>' if items else ""


def _cover(
    article: Article,
    lines: list[str],
    img: str | None,
    subject: SubjectAsset | None,
    stk: str,
    asset_dir: Path,
) -> str:
    title = KIND_TITLE.get(article.kind, "小火车日报")
    date = (
        f"{article.date[:2]}.{article.date[2:]}"
        if len(article.date) == 4
        else article.date
    )
    # The first cover line is the issue hook already represented by the title.
    # Keep only the remaining digest lines so the cover does not repeat screen 1.
    kick = [
        t
        for t in lines[1:]
        if t
        and t not in (title, "小火车日报")
        and not re.fullmatch(r"[\d.\-—/年月日 ]+", t)
    ][:3]
    fig, wide = "", False
    if subject and subject.mode == "cutout":
        cut = f"./{asset_dir.name}/{subject.rendered.name}"
        meta = f"./{asset_dir.name}/{subject.sidecar.name}"
        wh = _img_size(subject.rendered)
        wide = (
            bool(wh) and wh[0] > wh[1] * 1.6
        )  # 合影抠出来是横长条：铺满底部，字放上面
        fig = f'<img class="cut cover-cut{" wide" if wide else ""}" src="{cut}" alt="人物抠图" data-subject="people" data-subject-zone="{subject.zone}" data-subject-meta="{meta}" />'
    elif img:
        meta_attr = ""
        zone = "0,0,100,100"
        if subject:
            img = f"./{asset_dir.name}/{subject.rendered.name}"
            meta_attr = f' data-subject-meta="./{asset_dir.name}/{subject.sidecar.name}"'
            zone = subject.zone
        wh = _img_size(asset_dir / Path(img).name)
        wide = bool(wh) and wh[0] >= wh[1] * 1.15  # 横版整张铺满底部；竖版靠右
        fig = f'<img class="ph cover-ph{" wide" if wide else ""}" src="{img}" alt="封面图" data-subject="people" data-subject-zone="{zone}"{meta_attr} style="transform:rotate(-1.2deg)" />'
    layout = ' data-layout="subject-safe" data-overlay="forbid"' if fig else ""
    return (
        f'<section class="scr cover{" cover-wide" if wide else ""}" data-density="compact"{layout}>'
        f'<div class="title" data-occlusion="copy">{_esc(title)}</div>'
        f'<div class="date" data-occlusion="copy">{_esc(date)}</div>'
        f"{fig}"
        f'<div class="kick" data-occlusion="copy">{_big(kick, 1, "m", 470 if not wide else 900)}</div>'
        f"{stk}"
        f"</section>"
    )


def _screen(
    no: int,
    role: str,
    lines: list[str],
    main: str | None,
    extra: str | None,
    subject: SubjectAsset | None,
    extra_subject: SubjectAsset | None,
    is_shot: bool,
    extra_is_shot: bool,
    band: str | None,
    stickers: list[Path],
    asset_dir: Path,
) -> str:
    """一屏的排法，三种：
    over  ——横版照片铺满，字压在没脸的那条带子里（604 封面那种）
    side  ——截图/竖版/找不到空闲带：字一栏、图一栏
    cut   ——人物抠图立在一侧，字压过去
    """
    head = ""
    # 照片安全带的高度有限，叠字场景即使字少也保持横排，避免纵列越界压住人物。
    copy_flow = "horizontal" if band else _copy_flow(lines)
    copy_class = f"copy-{copy_flow}"
    side = "r" if no % 2 else "l"
    rot = (-2.2, 1.6, -1.1, 2.4)[no % 4]
    boxes = (
        faces(str(asset_dir / Path(main).name)) if main and not is_shot else ()
    )  # None = 没法检测
    wide_copy = _needs_wide_copy(lines)

    if subject and subject.mode == "cutout" and wide_copy:
        cut = f"./{asset_dir.name}/{subject.rendered.name}"
        meta = f"./{asset_dir.name}/{subject.sidecar.name}"
        fig = f'<img class="cut flow" src="{cut}" alt="人物抠图" data-subject="people" data-subject-zone="{subject.zone}" data-subject-meta="{meta}" />'
        slots = []
        body = f'{head}<div class="txt {copy_class} full-copy" data-copy-flow="{copy_flow}" data-occlusion="copy">{_big(lines, no, "l", 940)}</div>{fig}'
        cls = "copy-wide"
    elif subject and subject.mode == "cutout":
        cut = f"./{asset_dir.name}/{subject.rendered.name}"
        meta = f"./{asset_dir.name}/{subject.sidecar.name}"
        fig = f'<img class="cut {side}" src="{cut}" alt="人物抠图" data-subject="people" data-subject-zone="{subject.zone}" data-subject-meta="{meta}" />'
        slots = (
            ["bl", "ml"] if side == "r" else ["br", "mr"]
        )  # 顶部中间是文字起笔的地方，不落贴纸
        body = f'{head}{fig}<div class="txt {copy_class} has-fig-{side}" data-copy-flow="{copy_flow}" data-occlusion="copy">{_big(lines, no, "l", 430)}</div>'
        cls = "has-fig"
    elif main and band:
        copy_zone = "0,0,100,42" if band == "top" else "0,58,100,42"
        subject_zone = "0,42,100,58" if band == "top" else "0,0,100,58"
        fig = f'<img class="ph wide" src="{main}" alt="配图" data-subject="people" data-subject-zone="{subject_zone}" style="transform:rotate({rot * 0.4:.1f}deg)" />'
        txt_pos = "top" if band == "top" else "bottom"
        # 叠字照片已经占满整屏，贴纸无法证明不遮挡身体或关键动作，因此禁用。
        slots = []
        body = f'<div class="over-wrap">{fig}<div class="txt {copy_class} over {txt_pos}" data-copy-flow="{copy_flow}">{head}{_big(lines, no, "l", 660)}</div></div>'
        cls = "over"
    elif main and is_shot:
        fig = (
            f'<img class="shot stacked" src="{main}" alt="配图" data-subject="none" '
            f'data-subject-zone="0,0,100,100" style="transform:rotate({rot * 0.35:.1f}deg)" />'
        )
        slots = []
        body = f'{head}<div class="txt {copy_class} full-copy" data-copy-flow="{copy_flow}" data-occlusion="copy">{_big(lines, no, "l", 940)}</div>{fig}'
        cls = "shot-stack"
    elif main:
        meta_attr = ""
        zone = "0,0,100,100"
        if subject:
            main = f"./{asset_dir.name}/{subject.rendered.name}"
            meta_attr = f' data-subject-meta="./{asset_dir.name}/{subject.sidecar.name}"'
            zone = subject.zone
        kind = "shot" if is_shot else "ph"
        fig = (
            f'<img class="{kind} {side}" src="{main}" alt="配图" data-subject="{"none" if is_shot else "people"}" '
            f'data-subject-zone="{zone}"{meta_attr} style="transform:rotate({rot}deg)" />'
        )
        # 人物照片侧完全留空，贴纸只允许落在文字侧的边角。
        cands = (
            ["bl", "ml", "tl"] if side == "r" else ["br", "mr", "tr"]
        )  # 贴纸只能落在文字侧，不能覆盖人物照片。
        slots = [c for c in cands if is_shot or corner_free(boxes, c)]
        body = f'{head}{fig}<div class="txt {copy_class} has-fig-{side}" data-copy-flow="{copy_flow}" data-occlusion="copy">{_big(lines, no, "l", 430)}</div>'
        cls = "has-fig"
    else:
        slots = []
        body = f'{head}<div class="txt {copy_class}" data-copy-flow="{copy_flow}" data-occlusion="copy">{_big(lines, no, "l", 940)}</div>'
        cls = ""

    if extra:
        # 宽文案屏的第二张证据图进入文档流，不能沿用绝对定位压住人物或大字。
        slot = slots.pop(0) if slots else "br"
        extra_meta = ""
        extra_zone = "0,0,100,100"
        if extra_subject:
            extra = f"./{asset_dir.name}/{extra_subject.rendered.name}"
            extra_meta = f' data-subject-meta="./{asset_dir.name}/{extra_subject.sidecar.name}"'
            extra_zone = extra_subject.zone
        extra_kind = "none" if extra_is_shot else "people"
        if cls == "copy-wide":
            body += (
                f'<img class="ph flow-extra" src="{extra}" alt="配图" data-subject="{extra_kind}" data-subject-zone="{extra_zone}"{extra_meta} '
                f'style="transform:rotate({-rot * 0.35:.1f}deg)" />'
            )
        else:
            body += (
                f'<img class="ph small" src="{extra}" alt="配图" data-subject="{extra_kind}" data-subject-zone="{extra_zone}"{extra_meta} '
                f'style="{SLOT_CSS[slot]};transform:rotate({-rot}deg)" />'
            )
    body += (
        _stickers(stickers, asset_dir, slots, no)
        if main
        else _flow_stickers(stickers, asset_dir, no)
    )
    if band:
        layout = f' data-layout="subject-safe" data-overlay="zones" data-copy-zone="{copy_zone}"'
    else:
        layout = (
            ' data-layout="subject-safe" data-overlay="forbid"'
            if subject or (main and not is_shot) or extra
            else ""
        )
    return f'<section class="scr role-{role} {cls}" data-density="compact"{layout}>{body}</section>'


def _end(lines: list[str], stk: str) -> str:
    return (
        f'<section class="scr end" data-density="compact">'
        f'<div class="txt" data-occlusion="copy">{_big(lines, 2, "m", 900)}</div>{stk}'
        f'<div class="sig" data-platform-safe-zone="wechat-watermark-right-bottom">公众号 · 辛庄桥小火车</div>'
        f"</section>"
    )


def render_raw_html(article: Article, asset_dir: Path) -> str:
    asset_dir = Path(asset_dir)
    pool = _Pool(article.materials, asset_dir)
    style_dir = asset_dir.parent.parent / "style"
    lib = StickerLib(style_dir if style_dir.is_dir() else "style")
    reserved: dict[int, list[str]] = {}
    for idx, s in enumerate(article.screens):
        if s.role in {"cover", "end"}:
            continue
        got = pool.named_all(s.visual)
        got = [g for g in got if g]
        if got:
            reserved[idx] = got
    for idx, s in enumerate(article.screens):
        if s.role != "cover":
            continue
        got = [pool.named(s.visual, exact_only=True)]
        got = [g for g in got if g]
        if got:
            reserved[idx] = got
    def safe_subject(rel: str | None, is_screenshot: bool) -> SubjectAsset | None:
        """Preview may fall back; release probes reject missing metadata."""
        if not rel or is_screenshot:
            return None
        src = asset_dir / Path(rel).name
        try:
            return prepare_subject_asset(src, asset_dir)
        except SubjectMetadataError:
            return None

    def landscape(rel: str) -> bool:
        wh = _img_size(asset_dir / Path(rel).name)
        return bool(wh) and wh[0] >= wh[1] * 1.15

    parts: list[str] = []
    cover_lines, cover_img, cover_stk, cover_visual = [article.title], None, [], ""
    no = 0
    for idx, s in enumerate(article.screens):
        lines = _lines(s.text)
        if s.role == "cover":
            cover_lines, cover_visual = lines, s.visual
            cover_img = (reserved.get(idx) or [None])[0]
            cover_stk = lib.from_visual(s.visual) or [
                p for p in [lib.find(DEFAULT_STICKER["cover"])] if p
            ]
            continue
        if s.role == "end":
            parts.append(
                _end(
                    lines,
                    _stickers(lib.from_visual(s.visual), asset_dir, ["tl", "tr"], 7),
                )
            )
            continue
        no += 1
        imgs = list(reserved.get(idx, []))
        if not imgs and s.role in ("gossip", "headline"):
            imgs = [pool.screenshot()]
        elif not imgs and s.role in (
            "race",
            "earlybird",
            "feeling",
            "item",
            "preview",
            "review",
            "shirt",
        ):
            imgs = [pool.photo()]
        imgs = [i for i in imgs if i]
        main = imgs[0] if imgs else None
        extra = imgs[1] if len(imgs) > 1 else None
        is_shot = pool.is_screenshot(main)
        subject = safe_subject(main, is_shot)
        extra_is_shot = pool.is_screenshot(extra)
        extra_subject = safe_subject(extra, extra_is_shot)
        band = None
        # 人脸检测只能证明脸部安全，无法证明身体和服装标识安全；默认仍使用分栏。
        # 只有脚本明确声明“可叠字”时，才启用照片安全带叠层能力。
        if (
            main
            and not is_shot
            and not subject
            and landscape(main)
            and "可叠字" in s.visual
        ):
            band = free_band(faces(str(asset_dir / Path(main).name)))
        stickers = lib.from_visual(s.visual)
        parts.append(
            _screen(
                no, s.role, lines, main, extra, subject, extra_subject, is_shot, extra_is_shot, band, stickers, asset_dir
            )
        )
    cover_subject = safe_subject(cover_img, False)
    parts.insert(
        0,
        _cover(
            article,
            cover_lines,
            cover_img,
            cover_subject,
            "" if cover_img else _stickers(cover_stk, asset_dir, ["cr", "ml"], 3),
            asset_dir,
        ),
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" /><title>{_esc(article.title)}</title>
<style>{_CSS}
.page {{ background: {_bg(article)}; }}
</style></head><body><main class="page" data-platform-safe-zone="wechat-watermark-right-bottom">
{"".join(parts)}
</main></body></html>"""
