"""出稿自检：generate 之后自动跑，把机器写稿的常见毛病标出来写进稿件，给人工审核看。

个人号本来每篇都要人工过一遍；这里只是把该盯的地方先圈出来。
交给别人时，即使对方的 agent 直接生成不改，审稿人也能一眼看到「抄了样例原句」这类问题。

三类检查（都来自 21 篇实测 + benchmark 结论）：
1. copied：抄了 few-shot 真稿里的原句，而素材里并没有 → 最严重，往往是编造（如「我桥何时车神殿」）
2. bureaucratic：官腔戏仿超量（真稿中位 1/篇，反规则定 3/篇上限）
3. forbidden：项目约定不该出现的词（common-materials.yaml 里的 forbidden_replacements）
"""

from __future__ import annotations

import re

from .style import StyleAssets

_STRIP = r"[0-9a-zA-Z.:：，,。！!？?~、·\s「」\"'（）()【】\-—…]"
_BUREAU = re.compile(
    r"据悉|重点消息|小编锐评|应给予警告|保持关注|第一负责人|来报|表示[:：]|谁同意，谁反对|操作联系人"
)
_FORBIDDEN = ("第一张", "第二张", "骑就完了", "XHC")
# 喜子的固定口头禅，每期可以原样用，不算抄
_SIGNATURE = (
    "打不散的爱情吵不裂的友谊",
    "打不散的爱情",
    "吵不裂的友谊",
    "今天就到这里小编下班",
    "今天就到这里",
    "小编下班",
    "明天早鸟记得攻击",
    "赶紧上车",
    "大家都拥有了美好的一天",
    "让我们谢谢",
    "让我们祝福",
    "让我们恭喜",
    "这是人吗",
    "谁同意，谁反对",
    "留给小编",
    "的时间不多了",
    "写不下了",
    "先这么滴",
    "干就完了",
    "谴责",
    # 品牌词、固定信息栏、小编口头禅
    "辛庄桥小火车",
    "小火车日报",
    "小火车晚报",
    "小火车速报",
    "公众号",
    "领队",
    "收队",
    "集合",
    "辛庄桥",
    "小编只是搬运请勿攻击我",
    "小编只是搬运",
    "请勿攻击我",
    "小编也不知道谢谢",
    "小编也不知道",
    "谢谢",
    "小编锐评",
    "据悉",
    "重点消息",
    "头条",
    "祝xdm",
    "周末安全完赛",
    "下坡捏刹车",
    "长按二维码",
    "一触即发",
)


def _norm(s: str) -> str:
    return re.sub(_STRIP, "", s)


def _ngrams(text: str, n: int = 6) -> set[str]:
    t = _norm(text)
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def check(article, assets: StyleAssets, input_text: str = "") -> list[str]:
    """返回 warning 列表；空列表 = 干净。"""
    warns: list[str] = []
    # 0) 贴纸点名：库里没有的会在渲染时用那几个字生成一张举牌梗图，先提示一下
    try:
        from ..renderer.stickers import StickerLib

        lib = StickerLib(assets.style_dir)
        missing = [n for s in article.screens for n in lib.unknown(s.visual)]
        if missing:
            warns.append(
                f"[贴纸] 库里没有「{'」「'.join(dict.fromkeys(missing))}」，渲染时会拿这几个字做举牌梗图代替"
            )
    except Exception:
        pass
    gen = "\n".join(t for s in article.screens for t in s.text)
    gen_norm = _norm(gen)

    # 1) 抄样例原句：few-shot 里出现、素材里没有的连续 6 字
    src = _norm(input_text) + _norm(
        " ".join(m.text for m in article.materials if m.text)
    )
    # 样例真稿 + 说明书 + 黑话表里的例句，都算「不该原样搬」的来源
    shot_ngrams: set[str] = set()
    for _inp, out in assets.few_shots:
        shot_ngrams |= _ngrams(out)
    shot_ngrams |= _ngrams(assets.writing_style) | _ngrams(assets.slang)
    sig_norm = [_norm(s) for s in _SIGNATURE]

    def suspicious(g: str) -> bool:
        if g not in gen_norm or g in src:
            return False
        if any(g in s or s in g for s in sig_norm):  # 口头禅
            return False
        # 素材里有这些词只是换了语序 → 不算抄
        if any(g[i : i + 3] in src for i in range(len(g) - 2)):
            return False
        return True

    # 把命中的 6 字片段按在生成稿里的位置合并成整句
    hits = sorted(
        {
            (m.start(), m.end())
            for g in shot_ngrams
            if suspicious(g)
            for m in re.finditer(re.escape(g), gen_norm)
        }
    )
    spans: list[list[int]] = []
    for s, e in hits:
        if spans and s <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    for s, e in spans[:6]:
        warns.append(
            f"[抄样例] 「{gen_norm[s:e]}」在样例或说明书里出现过、素材里没有——可能是编造，核对事实"
        )

    # 2) 官腔超量
    n = len(_BUREAU.findall(gen))
    if n > 3:
        warns.append(
            f"[太官腔] 官腔戏仿 {n} 次（真稿中位 1、上限 3）——删几处「据悉/小编锐评/表示」"
        )

    # 3) 禁用词
    hit = [w for w in _FORBIDDEN if w in gen]
    if hit:
        warns.append(f"[用词] 出现约定不用的写法：{', '.join(hit)}")

    return warns
