from pathlib import Path

from xzq.models import SCREEN_ROLES
from xzq.writer.style import StyleAssets
from xzq.writer.prompts import _few_shot_block

ROOT = Path(__file__).resolve().parent.parent


def test_same_kind_few_shots_come_first():
    assets = StyleAssets.load(ROOT / "style")
    block = _few_shot_block(assets, "subao", max_n=3)
    # 速报有 3 篇真稿，前 3 篇应全是速报，不混进拼贴报；样例贴的是正文散文，不是 JSON
    assert block.count("### 真稿") == 3
    first = block.split("### 真稿 2")[0]
    assert "速报" in first and "小火车日报" not in first
    assert '"screens"' not in block


def test_unknown_kind_still_returns_examples():
    assets = StyleAssets.load(ROOT / "style")
    assert _few_shot_block(assets, "tuanjian", max_n=2).count("### 真稿") == 2


def test_all_few_shot_roles_follow_shared_contract():
    import json

    assets = StyleAssets.load(ROOT / "style")
    assert assets.few_shot_names[:3] == [
        "20240518-subao",
        "20240519-subao",
        "20240521-subao",
    ]
    roles = {
        screen["role"]
        for _input, output in assets.few_shots
        for screen in json.loads(output)["screens"]
    }
    assert roles <= SCREEN_ROLES
    assert {"headline", "earlybird", "item"} <= roles
