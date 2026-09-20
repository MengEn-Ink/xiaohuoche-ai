"""拼贴渲染器：图片分配（点名优先、每张一次）与版式选择。"""

from pathlib import Path

from xzq.models import Article, Material, Screen
from xzq.renderer.collage import render_collage_html


def _article(tmp_path: Path) -> Article:
    imgs = {}
    for name in ("合影.webp", "截图A.png", "截图B.png"):
        p = tmp_path / name
        p.write_bytes(b"x")
        imgs[name] = str(p)
    return Article(
        article_id="0101-ribao",
        kind="ribao",
        date="0101",
        title="0101，小火车日报。",
        materials=[
            Material(
                kind="photo", source=imgs["合影.webp"], meta={"filename": "合影.webp"}
            ),
            Material(
                kind="screenshot",
                source=imgs["截图A.png"],
                meta={"filename": "截图A.png"},
            ),
            Material(
                kind="screenshot",
                source=imgs["截图B.png"],
                meta={"filename": "截图B.png"},
            ),
        ],
        screens=[
            Screen(role="cover", text=["小火车日报"], visual="随便"),
            Screen(role="gossip", text=["第一条", "正文"], visual="不点名"),
            Screen(role="gossip", text=["第二条", "正文"], visual="贴 截图B.png 这张"),
            Screen(role="end", text=["小编下班"]),
        ],
    )


def test_named_image_is_reserved_even_if_later(tmp_path: Path):
    html = render_collage_html(_article(tmp_path), tmp_path / "assets")
    # 第二条点名了 截图B，第一条只能拿 截图A；封面拿合影
    assert html.index("合影.webp") < html.index("截图A.png") < html.index("截图B.png")
    assert html.count("<img") == 3


def test_each_image_used_once_and_layouts(tmp_path: Path):
    a = _article(tmp_path)
    a.screens.append(Screen(role="gossip", text=["第三条"], visual="没图了"))
    html = render_collage_html(a, tmp_path / "assets")
    # 内容屏先领图、每张只发一次：三个吃瓜屏把 3 张拿走
    assert html.count('class="story gossip"') == 3
    # 封面最后领，筐空了就复用第一张照片，不空着
    assert '<figure class="hero-cutout"' in html
    assert html.count("<img") == 4
    assert html.count('class="finale"') == 1


def test_index_reference_beats_filename_and_order(tmp_path: Path):
    """素材叫 IMG_001 这种没信息的名字、顺序乱，writer 写「用素材2」也能配对。"""
    imgs = {}
    for name in ("IMG_007.png", "IMG_002.png", "IMG_009.png"):
        p = tmp_path / name
        p.write_bytes(b"x")
        imgs[name] = str(p)
    a = Article(
        article_id="0101-ribao",
        kind="ribao",
        date="0101",
        title="t",
        materials=[
            Material(kind="draft", text="初稿"),  # 素材1
            Material(
                kind="photo",
                source=imgs["IMG_007.png"],
                meta={"filename": "IMG_007.png"},
            ),  # 素材2
            Material(
                kind="screenshot",
                source=imgs["IMG_002.png"],
                meta={"filename": "IMG_002.png"},
            ),  # 素材3
            Material(
                kind="photo",
                source=imgs["IMG_009.png"],
                meta={"filename": "IMG_009.png"},
            ),  # 素材4
        ],
        screens=[
            Screen(role="gossip", text=["A"], visual="用素材4 打底"),
            Screen(role="race", text=["B"], visual="素材 2"),
            Screen(role="end", text=["完"]),
        ],
    )
    html = render_collage_html(a, tmp_path / "assets")

    def section_of(marker: str) -> str:
        pos = html.index(marker)
        return html[html.rfind("<section", 0, pos) : html.index("</section>", pos)]

    assert "IMG_009.png" in section_of(">A<") and "IMG_007.png" in section_of(">B<")


def test_missing_candidate_falls_through_and_same_names_do_not_collide(tmp_path: Path):
    first = tmp_path / "missing" / "same.png"
    left = tmp_path / "left" / "same.png"
    right = tmp_path / "right" / "same.png"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"left")
    right.write_bytes(b"right")
    article = Article(
        "collision",
        "ribao",
        "0101",
        title="t",
        materials=[
            Material("photo", source=str(first)),
            Material("photo", source=str(left)),
            Material("photo", source=str(right)),
        ],
        screens=[Screen("race", ["一"]), Screen("race", ["二"])],
    )
    output = render_collage_html(article, tmp_path / "assets")
    copied = list((tmp_path / "assets").glob("*-same.png"))
    assert len(copied) == 2
    assert all(path.name in output for path in copied)


def test_stamp_replacement_does_not_nest():
    from xzq.renderer.collage import _esc

    output = _esc("谴责！谴责")
    assert output.count('class="badge-inline"') == 1
    assert "<span class=<span" not in output
