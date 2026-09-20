"""核心逻辑单测：状态机、审核门禁、风格加载、prompt 组装、离线生成。

不依赖网络与密钥；从仓库根目录运行：python -m pytest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import (
    _apply_collected_materials,
    _merge_materials,
    _positive_finite_float,
    _subject_safety_capability_errors,
    _validate_push_export,
)
from xzq.collector.inbox import list_inbox_images
from xzq.collector.strava import StravaClient, _club_slug
from xzq.llm import LLMClient
from xzq.models import Article, ArticleState, IllegalTransition, Screen
from xzq.reviewer.queue import ArticleQueue
from xzq.writer import prompts
from xzq.writer.generate import _parse_json, generate_script
from xzq.writer.style import StyleAssets

ROOT = Path(__file__).resolve().parent.parent


def test_subject_safety_capabilities_fail_closed_when_requirements_are_missing(
    tmp_path, monkeypatch
):
    import pipeline

    monkeypatch.setattr(
        pipeline.importlib.util,
        "find_spec",
        lambda name: None if name in {"rembg", "cv2", "playwright.sync_api"} else object(),
    )

    errors = _subject_safety_capability_errors(tmp_path / "missing-yunet.onnx")

    assert errors == [
        "缺少 Python 模块 rembg",
        "缺少 Python 模块 cv2",
        "缺少 Python 模块 playwright.sync_api",
        f"缺少 YuNet 模型 {tmp_path / 'missing-yunet.onnx'}",
    ]


def _article(kind="ribao", date="0913") -> Article:
    return Article(article_id=f"{date}-{kind}", kind=kind, date=date)


# ---------- 状态机 ----------


def test_legal_path_forward():
    a = _article()
    assert a.state == ArticleState.INBOX
    a.transition(ArticleState.DRAFT)
    a.screens = [Screen(role="cover", text=["x"])]
    a.review_checked = [True, True]
    a.approve(checklist_len=2)
    assert a.state == ArticleState.APPROVED
    a.transition(ArticleState.IMAGE)
    a.transition(ArticleState.PUSHED)
    a.transition(ArticleState.PUBLISHED)
    assert a.state == ArticleState.PUBLISHED


def test_illegal_skip_raises():
    a = _article()
    # inbox 不能直接跳到 approved（越步）
    with pytest.raises(IllegalTransition):
        a.transition(ArticleState.APPROVED)


def test_published_is_terminal():
    a = _article()
    a.transition(ArticleState.DRAFT)
    a.screens = [Screen(role="end", text=["x"])]
    a.review_checked = [True]
    a.approve(1)
    a.transition(ArticleState.IMAGE)
    a.transition(ArticleState.PUSHED)
    a.transition(ArticleState.PUBLISHED)
    with pytest.raises(IllegalTransition):
        a.transition(ArticleState.DRAFT)


def test_approve_requires_all_checked():
    a = _article()
    a.transition(ArticleState.DRAFT)
    a.screens = [Screen(role="cover", text=["x"])]
    a.review_checked = [True, False]  # 有一项没勾
    with pytest.raises(IllegalTransition):
        a.approve(2)
    # checklist 数量不匹配也不能通过
    a.review_checked = [True]
    with pytest.raises(IllegalTransition):
        a.approve(2)


def test_approve_requires_screens():
    a = _article()
    a.transition(ArticleState.DRAFT)
    a.review_checked = [True]
    with pytest.raises(IllegalTransition):
        a.approve(1)


def test_roundtrip_save_load(tmp_path):
    a = _article()
    a.transition(ArticleState.DRAFT)
    p = a.save(tmp_path / "a.json")
    b = Article.load(p)
    assert b.state == ArticleState.DRAFT
    assert b.article_id == a.article_id


# ---------- 风格资产 ----------


def test_style_assets_load():
    assets = StyleAssets.load(ROOT / "style")
    assert "小编" in assets.writing_style
    assert "莫拉" in assets.slang
    assert set(["ribao", "wanbao", "tuanjian"]).issubset(assets.templates)
    assert assets.template_for("ribao")  # 模板非空


# ---------- prompt 组装 ----------


def test_template_for_missing_kind_is_empty():
    assets = StyleAssets.load(ROOT / "style")
    assert assets.template_for("不存在的栏目") == ""


# ---------- 素材筐：图片 + 手机初稿 ----------


def test_inbox_reads_images_and_draft(tmp_path):
    (tmp_path / "群聊截图_老李.png").write_bytes(b"screenshot")
    (tmp_path / "合影.jpg").write_bytes(b"photo")
    (tmp_path / "draft.txt").write_text("今天黑四，曹科又拉爆了", encoding="utf-8")
    mats = list_inbox_images(tmp_path)
    kinds = {m.kind for m in mats}
    assert kinds == {"screenshot", "photo", "draft"}
    draft = next(m for m in mats if m.kind == "draft")
    assert "曹科" in draft.text


def test_draft_material_feeds_prompt():
    from xzq.models import Material

    assets = StyleAssets.load(ROOT / "style")
    materials = [Material(kind="draft", text="初稿：今天刷妙，早鸟很卷")]
    user = prompts.build_user_prompt(assets, "ribao", "0913", materials)
    assert "小编初稿" in user and "【初稿】" in user and "早鸟很卷" in user


def test_source_error_is_diagnostic_not_article_material():
    from xzq.models import Material

    assets = StyleAssets.load(ROOT / "style")
    materials = [
        Material(kind="draft", text="今天黑四"),
        Material(kind="source_error", text="[recent_prs 暂不可用：401]"),
    ]
    user = prompts.build_user_prompt(assets, "ribao", "0913", materials)
    assert "数据源诊断（只供判断缺口，禁止写进文章）" in user
    assert user.count("recent_prs 暂不可用") == 1


# ---------- Strava 解析与降级 ----------


def test_club_slug_accepts_url_and_slug():
    assert _club_slug("https://www.strava.com/clubs/Team_XZQ") == "Team_XZQ"
    assert _club_slug("https://www.strava.com/clubs/Team_XZQ/?x=1") == "Team_XZQ"
    assert _club_slug("Team_XZQ") == "Team_XZQ"


def test_resolve_club_id_by_slug(monkeypatch):
    client = StravaClient(
        client_id="1",
        client_secret="s",
        refresh_token="r",
        club_url="https://www.strava.com/clubs/Team_XZQ",
    )
    monkeypatch.setattr(
        client, "_get", lambda path, params=None: [{"id": 780580, "url": "Team_XZQ"}]
    )
    assert client.resolve_club_id() == "780580"


def test_resolve_club_id_paginates(monkeypatch):
    client = StravaClient(
        client_id="1", client_secret="s", refresh_token="r", club_url="Team_XZQ"
    )
    pages = {
        1: [{"id": index, "url": f"club-{index}"} for index in range(100)],
        2: [{"id": 780580, "url": "Team_XZQ"}],
    }
    monkeypatch.setattr(
        client,
        "_get",
        lambda path, params=None: pages.get(params["page"], []),
    )
    assert client.resolve_club_id() == "780580"


def test_resolve_club_id_accepts_numeric_url_without_listing_clubs(monkeypatch):
    client = StravaClient(
        client_id="1",
        client_secret="s",
        refresh_token="r",
        club_url="https://www.strava.com/clubs/780580",
    )
    monkeypatch.setattr(
        client,
        "_get",
        lambda *args, **kwargs: pytest.fail("数字 Club URL 不应请求俱乐部列表"),
    )
    assert client.resolve_club_id() == "780580"


def test_collect_all_keeps_success_when_other_sources_fail(monkeypatch):
    from xzq.models import Material

    client = StravaClient(client_id="1", client_secret="s", refresh_token="r")
    monkeypatch.setattr(
        client,
        "athlete_stats_material",
        lambda: Material(kind="strava", text="统计可用"),
    )
    monkeypatch.setattr(
        client, "club_activities", lambda: (_ for _ in ()).throw(RuntimeError("404"))
    )
    monkeypatch.setattr(
        client, "recent_prs", lambda: (_ for _ in ()).throw(RuntimeError("401"))
    )
    mats = client.collect_all()
    assert mats[0].text == "统计可用"
    assert [m.kind for m in mats].count("source_error") == 2


# ---------- 素材去重 ----------


def test_merge_materials_stable_dedup():
    from xzq.models import Material

    a = Material(kind="draft", source="draft.txt", text="今天黑四")
    b = Material(kind="photo", source="1.jpg", text="")
    merged = _merge_materials([a], [a, b])
    assert merged == [a, b]


def test_merge_materials_replaces_updated_source():
    from xzq.models import Material

    old = Material(kind="draft", source="draft.txt", text="旧初稿")
    new = Material(kind="draft", source="draft.txt", text="新初稿")
    merged = _merge_materials([old], [new])
    assert merged == [new]


def test_merge_materials_replaces_source_error_by_collector():
    from xzq.models import Material

    old = Material(kind="source_error", text="401", meta={"source": "recent_prs"})
    new = Material(kind="source_error", text="404", meta={"source": "recent_prs"})
    merged = _merge_materials([old], [new])
    assert merged == [new]


def test_merge_materials_snapshot_removes_deleted_and_recovered_sources():
    from xzq.models import Material

    old_draft = Material(kind="draft", source="deleted.txt", text="已删除")
    old_error = Material(kind="source_error", text="401", meta={"source": "recent_prs"})
    recovered = Material(kind="strava", source="effort-1", text="PR 已恢复")

    assert _merge_materials([old_draft, old_error], [recovered]) == [recovered]


def test_recollect_same_material_preserves_draft_and_review():
    from xzq.models import Material

    material = Material(kind="draft", source="draft.txt", text="今天黑四")
    article = _article()
    article.materials = [material]
    article.transition(ArticleState.DRAFT)
    article.screens = [Screen(role="cover", text=["已生成"])]
    article.review_checked = [True]

    changed = _apply_collected_materials(article, [material])

    assert changed is False
    assert article.state == ArticleState.DRAFT
    assert article.screens[0].text == ["已生成"]
    assert article.review_checked == [True]


def test_new_material_invalidates_existing_draft():
    from xzq.models import Material

    article = _article()
    article.materials = [Material(kind="draft", source="draft.txt", text="旧素材")]
    article.transition(ArticleState.DRAFT)
    article.screens = [Screen(role="cover", text=["旧稿"])]
    article.review_checked = [True]

    changed = _apply_collected_materials(
        article, [Material(kind="photo", source="new.jpg", text="新素材")]
    )

    assert changed is True
    assert article.state == ArticleState.INBOX
    assert article.screens == []
    assert article.review_checked == []


def test_build_prompt_contains_style_and_materials():
    assets = StyleAssets.load(ROOT / "style")
    from xzq.models import Material

    materials = [Material(kind="strava", text="曹科 黑四 PR 12:42", source="1")]
    system = prompts.build_system_prompt(assets)
    user = prompts.build_user_prompt(assets, "ribao", "0913", materials)
    assert "小编" in system and "莫拉" in system
    assert "0913" in user and "12:42" in user and "screens" in user


# ---------- 离线生成 ----------


def test_offline_generate_skeleton():
    assets = StyleAssets.load(ROOT / "style")
    llm = LLMClient()  # 无配置
    a = _article(kind="ribao")
    generate_script(a, assets, llm, offline=True)
    assert a.state == ArticleState.DRAFT
    assert len(a.screens) >= 3
    assert a.screens[0].role == "cover"
    # 离线骨架带占位标记
    assert all(s.data.get("_offline") for s in a.screens)


def test_regenerate_when_already_draft_stays_draft():
    assets = StyleAssets.load(ROOT / "style")
    a = _article(kind="wanbao")
    generate_script(a, assets, LLMClient(), offline=True)
    assert a.state == ArticleState.DRAFT
    # 再次生成不应因 DRAFT->DRAFT 非法跳转而报错
    generate_script(a, assets, LLMClient(), offline=True)
    assert a.state == ArticleState.DRAFT


def test_generate_removes_punctuation_only_duplicate_copy():
    class SequencedLLM:
        def __init__(self):
            self.replies = iter(
                [
                    '{"title":"0913，小火车日报｜苹果发布","screens":['
                    '{"role":"gossip","text":["苹果震撼发布爱疯 18 但没有 18",'
                    '"苹果震撼发布爱疯 18，但没有 18。"],"visual":"贴纸：熊猫头惊"}]}',
                    '{"screens":[{"visual":"贴纸：熊猫头惊"}]}',
                ]
            )

        def is_configured(self):
            return True

        def chat(self, system, user, temperature=0.9):
            return next(self.replies)

    article = _article()
    generate_script(article, StyleAssets(style_dir=ROOT / "style"), SequencedLLM())

    assert article.screens[0].text == ["苹果震撼发布爱疯 18 但没有 18"]


def test_parse_json_tolerates_fence():
    raw = '```json\n{"title": "x", "screens": []}\n```'
    assert _parse_json(raw)["title"] == "x"


# ---------- 审核队列 ----------


def test_queue_reject_back_to_inbox(tmp_path):
    q = ArticleQueue(tmp_path)
    a = _article()
    a.transition(ArticleState.DRAFT)
    a.screens = [Screen(role="cover", text=["x"])]
    q.save(a)
    q.reject(a.article_id, reason="素材不足")
    assert q.get(a.article_id).state == ArticleState.INBOX


def test_queue_approve_gates(tmp_path):
    q = ArticleQueue(tmp_path)
    a = _article()
    a.transition(ArticleState.DRAFT)
    a.screens = [Screen(role="cover", text=["x"])]
    q.init_checklist(a, ["数据正确", "隐私已处理", "允许发布"])
    q.save(a)
    # 未全勾 -> 拒绝
    with pytest.raises(IllegalTransition):
        q.approve(a.article_id, ["数据正确", "隐私已处理", "允许发布"])
    # 全勾 -> 通过
    art = q.get(a.article_id)
    art.review_checked = [True, True, True]
    q.save(art)
    assert (
        q.approve(a.article_id, ["数据正确", "隐私已处理", "允许发布"]).state
        == ArticleState.APPROVED
    )


def test_init_checklist_tracks_items_across_changes(tmp_path):
    q = ArticleQueue(tmp_path)
    article = _article()
    article.review_items = ["数据正确"]
    article.review_checked = [True]

    q.init_checklist(article, ["数据正确", "隐私已处理", "允许发布"])
    assert article.review_checked == [True, False, False]

    q.init_checklist(article, ["允许发布", "数据正确"])
    assert article.review_checked == [False, True]


# ---------- 发布与 watch 参数门禁 ----------


def test_push_rejects_stale_export_before_upload(tmp_path):
    article = Article(
        "stale",
        "ribao",
        "0913",
        state=ArticleState.IMAGE,
        screens=[Screen("cover", ["新正文"])],
        export_hash="旧指纹",
        image_path=str(tmp_path / "old.png"),
    )
    (tmp_path / "old.png").write_bytes(b"old")
    with pytest.raises(RuntimeError, match="版本不一致"):
        _validate_push_export(article)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "NaN", "inf", "-inf"])
def test_watch_interval_rejects_non_positive_or_non_finite(value):
    with pytest.raises(Exception, match="有限正数"):
        _positive_finite_float(value)


def test_watch_interval_accepts_positive_finite_value():
    assert _positive_finite_float("0.25") == 0.25


def test_parse_json_skips_preamble_fragments():
    """模型先写一段思考、里面夹了个小 {…}，再给正文 JSON——要取到正文。"""
    from xzq.writer.generate import _parse_json

    raw = '人名不确定留占位 {"甜菜根提问者":"待填"}。\n\n{"title": "0605，小火车日报。", "screens": [{"role": "end", "text": ["小编下班"], "visual": ""}]}'
    d = _parse_json(raw)
    assert d["title"] == "0605，小火车日报。" and len(d["screens"]) == 1


def test_system_prompt_uses_compact_slang():
    assets = StyleAssets(
        style_dir=ROOT / "style",
        writing_style="写作规则",
        slang="## 数据词\n不应进入\n## 固定称呼\n王教练\n## 常用句式\n小编下班\n## 其它\n不应进入",
    )
    system = prompts.build_system_prompt(assets)
    assert "王教练" in system and "小编下班" in system
    assert "不应进入" not in system


def test_run_auto_persists_rendered_article(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pipeline
    from xzq.renderer.to_longimage import article_content_hash

    queue = ArticleQueue(tmp_path)
    article = _article()
    article.transition(ArticleState.DRAFT)
    article.screens = [Screen("cover", ["正文"])]
    queue.save(article)

    class ConfigStub:
        review_checklist = ["事实准确"]

        @staticmethod
        def get(*_args, default=None):
            return "raw" if _args[:2] == ("renderer", "style") else default

    monkeypatch.setattr(
        pipeline, "_ctx", lambda args: (ConfigStub(), queue, None, None)
    )
    monkeypatch.setattr(pipeline, "cmd_collect", lambda args: None)
    monkeypatch.setattr(pipeline, "cmd_generate", lambda args: None)

    def fake_render(rendered, out_dir, style="raw"):
        image = tmp_path / "0913-ribao.png"
        image.write_bytes(b"png")
        cover = tmp_path / "0913-ribao-cover.jpg"
        cover.write_bytes(b"jpg")
        rendered.image_path = str(image)
        rendered.cover_path = str(cover)
        rendered.render_style = style
        rendered.transition(ArticleState.IMAGE)
        rendered.export_hash = rendered.content_hash = article_content_hash(
            rendered, style
        )
        return str(image)

    monkeypatch.setattr(pipeline, "render_long_image", fake_render)
    args = SimpleNamespace(date="0913", kind="ribao", auto=True, push=False)
    pipeline.cmd_run(args)

    saved = queue.get("0913-ribao")
    assert saved.state == ArticleState.IMAGE
    assert saved.image_path and saved.cover_path and saved.export_hash


def test_push_rejects_export_from_different_renderer_style(tmp_path):
    from xzq.renderer.to_longimage import article_content_hash

    image = tmp_path / "old.png"
    image.write_bytes(b"png")
    article = Article(
        "style-stale",
        "ribao",
        "0913",
        state=ArticleState.IMAGE,
        screens=[Screen("cover", ["正文"])],
        image_path=str(image),
        render_style="raw",
    )
    article.export_hash = article_content_hash(article, "raw")
    with pytest.raises(RuntimeError, match="版本不一致"):
        _validate_push_export(article, "0908")
