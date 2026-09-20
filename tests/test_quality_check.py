"""出稿自检：抄了说明书/样例里的句子要标出来，口头禅和素材原话不算。"""

from pathlib import Path

from xzq.models import Article, Material, Screen
from xzq.writer import quality
from xzq.writer.style import StyleAssets

ROOT = Path(__file__).resolve().parent.parent


def _art(lines, draft=""):
    return Article(
        article_id="t",
        kind="ribao",
        date="0101",
        materials=[Material(kind="draft", text=draft)],
        screens=[Screen(role="gossip", text=lines)],
    )


def _assets_with(shot_output: str) -> StyleAssets:
    """不依赖仓库里的真稿内容，测试自带一条样例。"""
    return StyleAssets(
        style_dir=ROOT / "style",
        writing_style="",
        slang="",
        few_shots=[("素材", shot_output)],
        few_shot_names=["x-ribao"],
    )


def test_flags_sentence_lifted_from_example():
    assets = _assets_with("扎胎人传人\n问：我桥何时车神殿？")
    ws = quality.check(
        _art(["王喆又扎胎了", "问：我桥何时车神殿？"], draft="王喆扎胎了"), assets
    )
    assert any("车神殿" in w for w in ws)


def test_signature_phrases_and_material_reorder_are_fine():
    assets = _assets_with("江浙沪精品六石红井绕圈\n打不散的爱情吵不裂的友谊")
    ws = quality.check(
        _art(
            ["六石红井绕圈江浙沪精品", "打不散的爱情，吵不裂的友谊。"],
            draft="江浙沪精品 Ride 六石红井绕圈",
        ),
        assets,
        "江浙沪精品 Ride 六石红井绕圈",
    )
    assert not [w for w in ws if "抄样例" in w]


def test_too_bureaucratic():
    assets = StyleAssets.load(ROOT / "style")
    ws = quality.check(_art(["据悉 A", "据悉 B", "小编锐评：C", "据悉 D"]), assets)
    assert any("太官腔" in w for w in ws)
