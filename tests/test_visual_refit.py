"""配图复核只改 visual、不动文字；条数对不上就原样保留。"""

import json

from xzq.models import Article, Material, Screen
from xzq.writer.generate import _refit_visuals


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def is_configured(self):
        return True

    def chat(self, system, user, temperature=0.9):
        self.calls.append(user)
        return self.reply


def _article():
    return Article(
        article_id="0908-ribao",
        kind="ribao",
        date="0908",
        materials=[
            Material(kind="draft", source="d.md", text="宽哥点评密云"),
            Material(
                kind="photo",
                source="x/kuan-review.webp",
                text="一名骑行者在吃面包",
                meta={"filename": "kuan-review.webp"},
            ),
        ],
        screens=[
            Screen(role="feeling", text=["宽哥锐评"], visual="贴纸：放大镜"),
            Screen(role="race", text=["周六妙峰山"], visual="用素材2"),
        ],
    )


def test_refit_replaces_visual_only():
    a = _article()
    llm = _LLM(
        json.dumps(
            {
                "screens": [
                    {"visual": "用素材2；贴纸：放大镜"},
                    {"visual": "贴纸：拿枪黄脸"},
                ]
            }
        )
    )
    _refit_visuals(a, llm)
    assert [s.visual for s in a.screens] == ["用素材2；贴纸：放大镜", "贴纸：拿枪黄脸"]
    assert a.screens[0].text == ["宽哥锐评"]
    assert "kuan-review.webp" in llm.calls[0]  # 文件名当线索给了模型


def test_refit_keeps_original_on_bad_reply():
    a = _article()
    _refit_visuals(a, _LLM("不是 JSON"))
    _refit_visuals(a, _LLM(json.dumps({"screens": [{"visual": "只有一条"}]})))
    assert [s.visual for s in a.screens] == ["贴纸：放大镜", "用素材2"]


def test_refit_keeps_visual_for_non_object_element():
    a = _article()
    reply = json.dumps({"screens": ["坏数据", {"visual": "贴纸：彩鸟"}]})
    _refit_visuals(a, _LLM(reply))
    assert [s.visual for s in a.screens] == ["贴纸：放大镜", "贴纸：彩鸟"]
