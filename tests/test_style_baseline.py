from __future__ import annotations

from pathlib import Path

from xzq.writer import prompts
from xzq.writer.generate import _offline_skeleton
from xzq.writer.style import StyleAssets

ROOT = Path(__file__).resolve().parents[1]


def test_daily_template_is_the_only_published_benchmark() -> None:
    template = (ROOT / "style/templates/ribao.md").read_text(encoding="utf-8")

    for required in (
        "605 真稿视觉基准",
        "style/references/605/",
        "0908",
        "0910",
        "拼贴实验版",
        'data-density="compact"',
        'data-subject="people|none"',
        'data-layout="subject-safe"',
        "data-subject-zone",
        'data-platform-safe-zone="wechat-watermark-right-bottom"',
        "最高优先级红线",
        "字多时必须横向成行",
        "只有总字数不超过 10 字",
        "正文展示字不小于 68px",
        "XZQ",
    ):
        assert required in template

    for stale in (
        "训练预告",
        "早鸟出生",
        "红、黄、蓝、绿、黑高饱和撞色",
        "固定口号 `干就完了`",
    ):
        assert stale not in template


def test_offline_daily_skeleton_matches_published_section_order() -> None:
    data = _offline_skeleton("ribao")
    screens = data["screens"]
    roles = [screen["role"] for screen in screens]

    assert roles == [
        "cover",
        "feeling",
        "gossip",
        "race",
        "review",
        "shirt",
        "preview",
        "end",
    ]
    assert "earlybird" not in roles
    assert all(screen["data"].get("_offline") is True for screen in screens)
    assert all("离线占位" in screen["text"][0] for screen in screens)


def test_history_demo_directories_are_removed() -> None:
    assert not (ROOT / "docs/demo").exists()
    assert not (ROOT / "data/demo").exists()

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/demo" not in ignore
    assert "data/demo" not in ignore


def test_repo_skill_points_to_605_ground_truth_and_preserves_experiments() -> None:
    skill = (ROOT / ".trae/skills/xiaohuoche-ribao-html/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "style/references/605/` 是唯一历史视觉 ground truth" in skill
    assert "0908/` 与 `0910/` 原样保留为拼贴实验版" in skill
    assert "不用厚黑线切屏" in skill
    assert "不使用栏目编号、白色信息卡阵列或英文装饰" in skill
    assert "红、黄、蓝、绿、黑高饱和对撞" not in skill
    assert "天蓝底" in skill
    assert "荧光绿、亮黄、白、粉紫、青、蓝紫" in skill
    assert "人物和文字双门禁" in skill
    assert "任意绘制层遮挡大字探针" in skill
    assert "单通道差值超过 18" in skill
    assert "变化比例超过 0.5%" in skill
    assert "source_sha256" in skill
    assert "文件字节 SHA256" in skill
    assert "封面与正文合并计算" in skill


def test_style_guidance_has_no_pre_605_palette_or_divider_copy() -> None:
    style_guide = (
        ROOT
        / ".trae/skills/xiaohuoche-ribao-html/references/style-guide.md"
    ).read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "用 7–10px 黑分隔线形成“长图分屏”" not in style_guide
    assert "复刻历史日报的红黄蓝绿黑手工拼贴排版" not in readme
    assert "单一天蓝底" in readme


def test_prompt_role_contract_matches_the_fixed_daily_template() -> None:
    assets = StyleAssets.load(ROOT / "style")
    prompt = prompts.build_user_prompt(assets, "ribao", "0914", [])

    # 0908 范本的 review/shirt 和 2024 真稿的 headline/earlybird/item 都可选，writer 按素材决定用哪些屏
    assert "review/shirt" in prompt and "earlybird" in prompt
    assert "字多时按完整语义组织成横向行" in prompt
    assert "只有总字数不超过 10 字的极短口号可做列式强调" in prompt
    assert "单人或双人主体照片优先写「抠图」" in prompt
    assert "不得被文字、色块或贴纸遮挡" in prompt
