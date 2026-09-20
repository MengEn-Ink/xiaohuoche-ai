"""Prompt 组装：风格说明书 + 黑话词典 + 栏目模板 + few-shot + 当周素材。"""

from __future__ import annotations

import json

from ..models import Material
from .style import StyleAssets

SYSTEM_PREFIX = (
    "你是骑行俱乐部公众号「辛庄桥小火车」的御用小编，负责按既有风格写推文分屏脚本。"
)


def build_system_prompt(assets: StyleAssets) -> str:
    parts = [
        SYSTEM_PREFIX,
        "",
        assets.writing_style,
        "",
        "## 骑行黑话词典",
        _slang_compact(assets.slang),
    ]
    return "\n".join(p for p in parts if p)


def _slang_compact(slang: str) -> str:
    """黑话词典 53 行表塞进 prompt 会让模型「用力用词」；只留固定称呼和常用句式，词本身从真稿里学。"""
    keep = []
    for sec in ("## 固定称呼", "## 常用句式"):
        i = slang.find(sec)
        if i >= 0:
            j = slang.find("\n## ", i + 1)
            keep.append(slang[i : j if j > 0 else None].strip())
    return "\n\n".join(keep) if keep else slang


def build_user_prompt(
    assets: StyleAssets,
    kind: str,
    date: str,
    materials: list[Material],
    title: str = "",
) -> str:
    few_shot_block = _few_shot_block(assets, kind)
    material_block = _material_block(materials)
    diagnostics_block = _diagnostics_block(materials)
    draft_block = _draft_block(materials)
    from datetime import date as _date

    year = _date.today().year
    shown = (
        f"{year}-{date[:2]}-{date[2:]}" if len(date) == 4 and date.isdigit() else date
    )
    lines = [
        f"栏目：{kind}（日期 {shown}，标题里只写 {date} 这种月日形式）"
        + (f"，标题：{title}" if title else ""),
    ]
    # 栏目模板故意不喂给模型：盲判实验里带模板的稿子更容易被认出是 AI（模板句式会原样出现），
    # 分屏结构靠历史真稿示范就够了。模板文件保留给 style.py 做离线兜底判断。
    if draft_block:
        lines += ["", "## 小编初稿（最高优先级）", draft_block]
    if diagnostics_block:
        lines += [
            "",
            "## 数据源诊断（只供判断缺口，禁止写进文章）",
            diagnostics_block,
        ]
    if few_shot_block:
        lines += ["", "## 历史真稿（怎么说话看这里；梗和事实不要照抄）", few_shot_block]
    sticker_menu = _sticker_menu(assets)
    if sticker_menu:
        lines += ["", "## 可用贴纸 / 梗图", sticker_menu]
    lines += [
        "",
        "## 当周素材",
        material_block or "（暂无素材，按模板出占位骨架，人名/成绩留空）",
        "",
        "## 输出要求",
        '只输出 JSON，结构：{"title": str, "screens": [{"role": str, '
        '"text": [str, ...], "visual": str, "data": {}}]}。'
        "role 从 cover/headline/preview/race/earlybird/gossip/feeling/review/shirt/item/checklist/end 中选（item 专用于速报体编号条目）；"
        "text 是这屏的文案数组：一屏只讲一件事，标题已经完整说清时不要再写标点不同的同义正文；字多时按完整语义组织成横向行，不要拆成竖排、窄列或逐字短行；只有总字数不超过 10 字的极短口号可做列式强调。visual 写这屏用哪些素材——用编号写「用素材3」或「用素材3和素材5」（照片和截图都有编号，和文件名、顺序无关），一屏最多两张，每张只能用一次；贴纸每屏 1 到 3 张、封面和结尾也要，写「贴纸：熊猫头惊」（只写名字，别加括号说明）；单人或双人主体照片优先写「抠图」以裁透明空边后放大，合影别抠；人物脸、身体主体、服装标识和关键动作不得被文字、色块或贴纸遮挡；需要梗图写「梗图：喜报|文字」；data 放需人工填的占位。"
        "配图必须和这屏说的事对得上（看素材描述和文件名），对不上宁可只贴贴纸不配图。功率/PR/时间只能来自素材里的 Strava 数据，不许编造；人名不确定就留占位。",
    ]
    if draft_block:
        lines.append(
            "【初稿】以上是小编随手记的事实清单。把它换成本号的说法、拆成分屏——"
            "不扩写、不解释梗、不给每条都配包袱，大部分句子就是平铺事实，说完就走；"
            "初稿里没有的细节不要编造，缺数据就留占位；初稿原话里的 emoji 原样带上。"
        )
    return "\n".join(lines)


def _draft_block(materials: list[Material]) -> str:
    drafts = [m for m in materials if m.kind == "draft" and m.text]
    if not drafts:
        return ""
    return "\n\n".join(
        f"（{m.meta.get('filename', '初稿')}）\n{m.text}" for m in drafts
    )


def _few_shot_block(assets: StyleAssets, kind: str = "", max_n: int = 5) -> str:
    """同栏目的真稿优先，不够再用其它栏目补；速报体和拼贴报差别大，混喂会串味。"""
    if not assets.few_shots:
        return ""
    names = assets.few_shot_names or [""] * len(assets.few_shots)
    paired = list(zip(names, assets.few_shots))
    same = [p for p in paired if kind and p[0].endswith(f"-{kind}")]
    rest = [p for p in paired if p not in same]
    chosen = (same + rest)[:max_n]
    blocks = []
    for i, (_name, (_inp, out)) in enumerate(chosen, 1):
        blocks.append(f"### 真稿 {i}\n{_flatten(out)}")
    return "\n\n".join(blocks)


def _flatten(output_json: str) -> str:
    """分屏脚本 JSON → 读者看到的正文（标题 + 逐屏文案）。模型看散文学嗓音，看 JSON 只会学结构。"""
    try:
        d = json.loads(output_json)
    except Exception:
        return output_json
    lines = [d.get("title", "")]
    for s in d.get("screens", []):
        lines.extend(t for t in (s.get("text") or []) if t)
        lines.append("")
    return "\n".join(lines).strip()


def _diagnostics_block(materials: list[Material]) -> str:
    errors = [m.text for m in materials if m.kind == "source_error" and m.text]
    return "\n".join(errors)


def _material_block(materials: list[Material]) -> str:
    content_materials = [m for m in materials if m.kind != "source_error"]
    if not content_materials:
        return ""
    lines = []
    for i, m in enumerate(content_materials, 1):
        kind = {
            "draft": "初稿",
            "photo": "照片",
            "screenshot": "截图",
            "strava": "Strava",
        }.get(m.kind, m.kind)
        fn = m.meta.get("filename") or (m.source.rsplit("/", 1)[-1] if m.source else "")
        head = f"素材{i}［{kind}］" + (
            f"（文件名 {fn}）" if fn and m.kind != "draft" else ""
        )
        lines.append(f"{head}\n   {m.text}" if m.text else head)
    return "\n".join(lines)


def build_visual_fix_prompt(materials: list[Material], screens) -> tuple[str, str]:
    """出稿后的配图复核：只改每屏的 visual（配哪张素材、贴什么贴纸），文字一字不动。"""
    system = (
        "你是排版编辑。下面是一期分屏脚本和素材清单，检查每屏配图是否和这屏说的事对得上。"
        '只输出 JSON：{"screens": [{"visual": str}, ...]}，条数和顺序与输入一致，text 不要动、不要输出。'
    )
    rules = (
        "规则：1) 一屏只讲一件事；素材描述或文件名和这屏全部文字说的是同一件事才配，对不上就不配图，只留贴纸，禁止把另一个事件的图或文案拼进来；"
        "2) 每张素材全篇最多用一次，写「用素材N」；3) 贴纸写「贴纸：名字」，一屏 1 到 3 张；"
        "4) 一屏没图没贴纸不行，至少一张贴纸；5) 封面不点素材，只写贴纸。"
    )
    scr = "\n".join(
        f"第{i}屏［{s.role}］ visual=「{s.visual}」\n   文字：{' / '.join(s.text)}"
        for i, s in enumerate(screens, 1)
    )
    user = f"## 素材清单\n{_material_block(materials)}\n\n## 分屏脚本\n{scr}\n\n{rules}"
    return system, user


def _sticker_menu(assets: StyleAssets) -> str:
    try:
        from ..renderer.stickers import StickerLib

        lib = StickerLib(assets.style_dir)
        return lib.menu() if lib.items else ""
    except Exception:  # 贴纸库不是必需品，缺了照常出稿
        return ""
