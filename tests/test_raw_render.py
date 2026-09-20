"""真日报版式：无卡片、大字自适应字号、有图的屏撑高、封面不重复日期、rembg 缺失不报错。"""

from pathlib import Path

from xzq.models import Article, Material, Screen
from xzq.renderer.raw import _copy_flow, _fit, render_raw_html
from xzq.renderer.cutout import SubjectAsset


def test_copy_flow_keeps_long_copy_in_rows_and_allows_short_columns():
    assert _copy_flow(["多来点江浙沪骑法", "不要学那屯子傻骑战法"]) == "horizontal"
    assert _copy_flow(["拿 KOM", "爆金币"]) == "vertical"
    assert _copy_flow(["这是一句超过十个字的完整文案"]) == "horizontal"


def test_fit_preserves_large_type_and_wraps_long_copy():
    assert _fit("四个字", 980, 96) == 96
    assert _fit("这是一句很长很长很长很长很长的中文标题", 540, 96) == 88


def test_render_raw_structure(tmp_path: Path):
    img = tmp_path / "合影.png"
    from PIL import Image

    Image.new("RGB", (40, 30), (200, 100, 50)).save(img)
    a = Article(
        article_id="0101-ribao",
        kind="ribao",
        date="0101",
        title="0101，小火车日报。",
        materials=[
            Material(kind="photo", source=str(img), meta={"filename": "合影.png"})
        ],
        screens=[
            Screen(
                role="cover", text=["小火车日报", "2026.01.01", "今天真好"], visual=""
            ),
            Screen(
                role="gossip",
                text=["第一条", "正文很长很长很长很长很长很长很长"],
                visual="合影.png",
            ),
            Screen(role="end", text=["小编下班"]),
        ],
    )
    html = render_raw_html(a, tmp_path / "assets")
    assert 'class="panel"' not in html  # 没有卡片
    assert html.count('<section class="scr') == 3
    assert "has-fig" in html  # 有图的屏撑高
    assert (
        "2026.01.01" not in html.split("kick")[1].split("</div>")[0]
    )  # 封面 kick 不重复日期
    assert 'class="scr end"' in html
    assert 'data-layout="subject-safe" data-overlay="forbid"' in html
    assert 'data-copy-flow="horizontal"' in html
    assert ".cut { position: absolute; z-index: 4;" in html
    assert ".txt { position: relative; z-index: 2;" in html
    assert "max-width: 58%" in html
    assert 'class="lab"' not in html
    assert "重点消息" not in html
    assert "scr + .scr { border-top" not in html
    assert 'class="tear-edge"' not in html


def test_cover_does_not_reuse_body_photo_when_pool_is_empty(tmp_path: Path):
    """只有 1 张照片时留给正文，封面宁可纯排版也不能重复图片。"""
    img = tmp_path / "合影.png"
    from PIL import Image

    Image.new("RGB", (40, 30), (200, 100, 50)).save(img)
    a = Article(
        article_id="0102-ribao",
        kind="ribao",
        date="0102",
        title="0102，小火车日报。",
        materials=[
            Material(kind="photo", source=str(img), meta={"filename": "合影.png"})
        ],
        screens=[
            Screen(role="cover", text=["小火车日报"], visual=""),
            Screen(role="race", text=["战报"], visual="合影.png"),
            Screen(role="end", text=["小编下班"]),
        ],
    )
    html = render_raw_html(a, tmp_path / "assets")
    cover = html.split('<section class="scr')[1]
    body = html.split('<section class="scr')[2]
    assert "合影" not in cover
    assert "合影" in body
    assert html.count("data-subject-meta") == 1


def test_cover_kick_uses_digest_without_repeating_primary_hook(tmp_path: Path):
    article = Article(
        "cover-digest",
        "ribao",
        "0910",
        title="0910，小火车日报｜冻成孙子再等几分钟",
        screens=[
            Screen(
                role="cover",
                text=["早鸟冻成孙子再等几分钟", "洒水扑街｜群聊搞钱"],
            )
        ],
    )

    html = render_raw_html(article, tmp_path / "assets")
    kick = html.split('class="kick"', 1)[1].split("</section>", 1)[0]

    assert "早鸟冻成孙子再等几分钟" not in kick
    assert "洒水扑街｜群聊搞钱" in kick


def test_cover_and_end_take_stickers_and_landscape_photo_gets_text_over(
    tmp_path: Path, monkeypatch
):
    from PIL import Image
    from xzq.renderer import raw

    img = tmp_path / "IMG_1.png"
    Image.new("RGB", (400, 200), (120, 120, 200)).save(img)  # 横版
    monkeypatch.setattr(
        raw, "faces", lambda p: ((0.45, 0.5, 0.1, 0.3),)
    )  # 脸在下半，字该压上带
    monkeypatch.setattr(raw, "prepare_subject_asset", lambda src, dst: None)
    a = Article(
        article_id="0103-ribao",
        kind="ribao",
        date="0103",
        title="0103，小火车日报。",
        materials=[
            Material(kind="photo", source=str(img), meta={"filename": "IMG_1.png"})
        ],
        screens=[
            Screen(role="cover", text=["小火车日报"], visual="贴纸：王咪猫"),
            Screen(
                role="item",
                text=["一句"],
                visual="用素材1；可叠字；贴纸：熊猫头惊；贴纸：放大镜",
            ),
            Screen(role="end", text=["小编下班"], visual="贴纸：彩鸟"),
        ],
    )
    html = raw.render_raw_html(a, tmp_path / "assets")
    cover, body, end = html.split('<section class="scr')[1:4]
    assert "cat_wangmi" in cover and "领队" not in cover
    assert (
        'class="txt copy-horizontal over top"' in body
        and 'data-overlay="zones" data-copy-zone="0,0,100,42"' in body
        and 'data-subject-zone="0,42,100,58"' in body
        and "panda_shock" not in body
        and "magnifier" not in body
    )
    assert "bird_color" in end


def test_long_raw_copy_wraps_instead_of_clipping():
    from xzq.renderer.raw import _big

    output = _big(["很长" * 40], width_px=540)
    assert "font-size:88px" in output
    assert "white-space:normal;overflow-wrap:anywhere" in output


def test_raw_type_contract_keeps_large_bright_readable_headlines():
    from xzq.renderer import raw

    assert "-webkit-text-stroke: 3px #101820" in raw._CSS
    assert "paint-order: stroke fill" in raw._CSS
    assert "text-shadow: 8px 8px 0 #101820" in raw._CSS
    assert "-webkit-text-stroke: 7px" not in raw._CSS
    assert raw.LINE_COLORS == ["#EAFF00", "#FFFFFF", "#25F4EE", "#FF5FB0"]
    assert "#755cff" not in {color.lower() for color in raw.LINE_COLORS}


def test_landscape_cover_keeps_minimum_height(tmp_path: Path):
    img = tmp_path / "wide.png"
    from PIL import Image

    Image.new("RGBA", (400, 100), (1, 2, 3, 255)).save(img)
    article = Article(
        "wide-cover",
        "ribao",
        "0104",
        title="标题",
        materials=[Material(kind="photo", source=str(img))],
        screens=[Screen("cover", ["标题"], visual="用素材1")],
    )
    output = render_raw_html(article, tmp_path / "assets")
    assert "cover-wide" in output
    assert ".cover-wide { min-height: 1080px;" in output
    assert ".scr:not(.cover):not(.has-fig):not(.over):not(.end)" in output


def test_raw_reuses_precut_people_as_large_safe_subject(tmp_path: Path, monkeypatch):
    from PIL import Image
    from xzq.renderer import cutout as cutout_module

    monkeypatch.setattr(cutout_module, "faces", lambda path: ())

    img = tmp_path / "people.png"
    image = Image.new("RGBA", (120, 180), (0, 0, 0, 0))
    for x in range(15, 105):
        for y in range(8, 178):
            image.putpixel((x, y), (100, 120, 140, 255))
    image.save(img)
    second = tmp_path / "shirt.png"
    image.save(second)
    article = Article(
        article_id="0105-ribao",
        kind="ribao",
        date="0105",
        title="0105，小火车日报。",
        materials=[
            Material(kind="photo", source=str(img)),
            Material(kind="photo", source=str(second)),
        ],
        screens=[
            Screen(role="cover", text=["小火车日报"], visual="用素材1；人物抠图"),
            Screen(role="shirt", text=["大字不挡人"], visual="用素材2；人物抠图"),
        ],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert output.count('class="cut') == 2
    assert output.count('src="./assets/') == 2
    assert output.count('data-subject-meta="./assets/') == 2
    assert output.count('data-layout="subject-safe" data-overlay="forbid"') == 2
    assert "max-width: 58%" in output
    assert "font-size:88px" in output


def test_raw_attempts_safe_cutout_for_ordinary_photo_without_hint(tmp_path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    source = tmp_path / "rider.jpg"
    Image.new("RGB", (80, 120), "navy").save(source)
    rendered = tmp_path / "assets" / "rider-outlined.png"
    rendered.parent.mkdir()
    Image.new("RGBA", (60, 110), (1, 2, 3, 255)).save(rendered)
    sidecar = Path(str(rendered) + ".subject.json")
    sidecar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        raw, "prepare_subject_asset",
        lambda src, dst: SubjectAsset(rendered, sidecar, "10,5,80,90", "cutout"),
    )
    article = Article(
        "default-cutout", "ribao", "0106", title="标题",
        materials=[Material(kind="photo", source=str(source))],
        screens=[Screen("cover", ["标题"]), Screen("race", ["正文"], visual="用素材1")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert "rider-outlined.png" in output
    assert 'data-subject-zone="10,5,80,90"' in output
    assert 'data-subject-meta="./assets/rider-outlined.png.subject.json"' in output


def test_cover_reference_does_not_consume_body_named_material(tmp_path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    Image.new("RGB", (80, 120), "navy").save(first)
    Image.new("RGB", (80, 120), "green").save(second)
    monkeypatch.setattr(raw, "prepare_subject_asset", lambda src, dst: None)
    article = Article(
        "cover-reservation", "ribao", "0107", title="标题",
        materials=[Material(kind="photo", source=str(first)), Material(kind="photo", source=str(second))],
        screens=[
            Screen("cover", ["标题"], visual="用素材1"),
            Screen("race", ["正文"], visual="用素材1"),
        ],
    )

    output = render_raw_html(article, tmp_path / "assets")
    _, cover, body = output.split('<section class="scr', 2)

    assert "first.jpg" in body
    assert "first.jpg" not in cover and "second.jpg" not in cover


def test_second_photo_gets_its_own_subject_sidecar(tmp_path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    Image.new("RGB", (80, 120), "navy").save(first)
    Image.new("RGB", (80, 120), "green").save(second)

    def prepared(src, dst):
        rendered = dst / f"{src.stem}-outlined.png"
        Image.new("RGBA", (60, 110), (1, 2, 3, 255)).save(rendered)
        sidecar = Path(str(rendered) + ".subject.json")
        sidecar.write_text("{}", encoding="utf-8")
        return SubjectAsset(rendered, sidecar, "10,5,80,90", "cutout")

    monkeypatch.setattr(raw, "prepare_subject_asset", prepared)
    article = Article(
        "two-photos", "ribao", "0108", title="标题",
        materials=[Material(kind="photo", source=str(first)), Material(kind="photo", source=str(second))],
        screens=[Screen("race", ["正文"], visual="用素材1和素材2")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    _, _cover, body = output.split('<section class="scr', 2)
    assert body.count('data-subject-meta="./assets/') == 2
    assert "first-outlined.png" in body
    assert "second-outlined.png" in body


def test_second_screenshot_is_never_cut_out(tmp_path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    photo = tmp_path / "photo.jpg"
    screenshot = tmp_path / "chat.png"
    Image.new("RGB", (80, 120), "navy").save(photo)
    Image.new("RGB", (80, 120), "white").save(screenshot)
    calls = []

    def prepared(src, dst):
        calls.append(Path(src).name)
        rendered = dst / f"{src.stem}-outlined.png"
        Image.new("RGBA", (60, 110), (1, 2, 3, 255)).save(rendered)
        sidecar = Path(str(rendered) + ".subject.json")
        sidecar.write_text("{}", encoding="utf-8")
        return SubjectAsset(rendered, sidecar, "10,5,80,90", "cutout")

    monkeypatch.setattr(raw, "prepare_subject_asset", prepared)
    article = Article(
        "photo-shot", "ribao", "0109", title="标题",
        materials=[Material(kind="photo", source=str(photo)), Material(kind="screenshot", source=str(screenshot))],
        screens=[Screen("race", ["正文"], visual="用素材1和素材2")],
    )

    output = render_raw_html(article, tmp_path / "assets")
    _, _cover, body = output.split('<section class="scr', 2)

    assert calls and all("chat" not in name for name in calls)
    assert 'src="./assets/' in body and "chat.png" in body
    assert 'alt="配图" data-subject="none"' in body


def test_wide_cutout_keeps_secondary_screenshot_in_document_flow(tmp_path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    photo = tmp_path / "photo.png"
    shot = tmp_path / "chat.png"
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(photo)
    Image.new("RGB", (720, 1280), "white").save(shot)
    rendered = tmp_path / "assets" / "photo-outlined.png"
    rendered.parent.mkdir()
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(rendered)
    sidecar = Path(str(rendered) + ".subject.json")
    sidecar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        raw, "prepare_subject_asset",
        lambda src, dst: SubjectAsset(rendered, sidecar, "0,0,100,100", "cutout"),
    )
    article = Article(
        "flow-extra", "ribao", "0910", title="标题",
        materials=[Material("photo", source=str(photo)), Material("screenshot", source=str(shot))],
        screens=[Screen("gossip", ["Kohachi 放坡扑街", "等我几分钟。"], visual="用素材1和素材2")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="ph flow-extra"' in output
    assert output.index('class="cut flow"') < output.index('class="ph flow-extra"')


def test_screenshot_uses_stacked_layout_so_copy_keeps_full_width(tmp_path: Path):
    from PIL import Image

    screenshot = tmp_path / "chat.png"
    Image.new("RGB", (720, 1280), "white").save(screenshot)
    article = Article(
        "shot-stack", "ribao", "0910", title="标题",
        materials=[Material(kind="screenshot", source=str(screenshot))],
        screens=[Screen("preview", ["十一假期可能有戒潭活动", "Ouch Wang - Ride Leader"], visual="用素材1")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="scr role-preview shot-stack"' in output
    assert 'class="txt copy-horizontal full-copy"' in output
    assert 'class="shot stacked"' in output
    assert output.index('class="txt copy-horizontal full-copy"') < output.index('class="shot stacked"')
    assert ".role-preview.shot-stack::after { content: none; }" in output


def test_long_copy_with_cutout_uses_wide_flow_layout(tmp_path: Path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    source = tmp_path / "rider.png"
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(source)
    rendered = tmp_path / "assets" / "rider-outlined.png"
    rendered.parent.mkdir()
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(rendered)
    sidecar = Path(str(rendered) + ".subject.json")
    sidecar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        raw, "prepare_subject_asset",
        lambda src, dst: SubjectAsset(rendered, sidecar, "0,0,100,100", "cutout"),
    )
    article = Article(
        "wide-copy", "ribao", "0910", title="标题",
        materials=[Material(kind="photo", source=str(source))],
        screens=[Screen("gossip", ["Kohachi 放坡扑街之后还要再等我几分钟"], visual="用素材1")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="scr role-gossip copy-wide"' in output
    assert 'class="txt copy-horizontal full-copy"' in output
    assert output.index('class="txt copy-horizontal full-copy"') < output.index('class="cut flow"')


def test_two_medium_lines_with_cutout_use_wide_flow_layout(tmp_path: Path, monkeypatch):
    from PIL import Image
    from xzq.renderer import raw

    source = tmp_path / "rider.png"
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(source)
    rendered = tmp_path / "assets" / "rider-outlined.png"
    rendered.parent.mkdir()
    Image.new("RGBA", (120, 180), (10, 20, 30, 255)).save(rendered)
    sidecar = Path(str(rendered) + ".subject.json")
    sidecar.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        raw, "prepare_subject_asset",
        lambda src, dst: SubjectAsset(rendered, sidecar, "0,0,100,100", "cutout"),
    )
    article = Article(
        "wide-two-lines", "ribao", "0910", title="标题",
        materials=[Material(kind="photo", source=str(source))],
        screens=[Screen("gossip", ["Kohachi 放坡扑街", "等我几分钟。"], visual="用素材1")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="scr role-gossip copy-wide"' in output


def test_every_display_copy_block_is_marked_for_raster_probe(tmp_path: Path):
    article = Article(
        "copy-contract", "ribao", "0910", title="标题",
        screens=[Screen("race", ["周六妙峰山比赛，现在上啥科技有用？"], visual="贴纸：熊猫头惊")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="txt copy-horizontal" data-copy-flow="horizontal" data-occlusion="copy"' in output


def test_end_screen_copy_is_marked_for_raster_probe(tmp_path: Path):
    article = Article(
        "end-copy-contract", "ribao", "0910", title="标题",
        screens=[Screen("end", ["周六妙峰山比赛，现在上啥科技有用？"])],
    )

    output = render_raw_html(article, tmp_path / "assets")

    end = output.split('class="scr end"', 1)[1].split("</section>", 1)[0]
    assert 'class="txt" data-occlusion="copy"' in end


def test_plain_screen_stickers_flow_after_copy_and_no_default_tear_edge(tmp_path: Path):
    article = Article(
        "safe-stickers", "ribao", "0910", title="标题",
        screens=[Screen("race", ["周六妙峰山比赛，现在上啥科技有用？"], visual="贴纸：熊猫头惊")],
    )

    output = render_raw_html(article, tmp_path / "assets")

    assert 'class="sticker-row"' in output
    assert output.index('class="txt copy-horizontal"') < output.index('class="sticker-row"')
    assert 'class="tear-edge"' not in output
    assert ".scr.role-race:not(.shot-stack) { display: grid;" in output
    assert "grid-template-columns: minmax(0, 1fr) 190px;" in output
