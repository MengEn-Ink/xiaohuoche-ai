from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "docs" / "ribao-html" / "0912"


def test_0912_uses_every_supplied_photo_once_with_original_bytes() -> None:
    expected = {
        "01-podium.jpg": "626573dc64545219fec4102971f6971153bfceb258ba3ebda445236a228d3b6b",
        "02-race.jpg": "2677a61f92f61f8600e599502d525cb27bd5830b8721c8f1a0d23a2e5aaefc48",
        "03-finish.jpg": "49414407e68194dfbe564a3dfdd2962df9b9d3d26a44608ac78a3e262b60f18f",
        "04-group-podium.jpg": "b88221414eca1be304df57d449c6ae0ecd214520890bcbde4150c7d3d7cf8df3",
        "05-group-bike.jpg": "e16b3ae0914de8c2164b620624f2a6b7d77b7bc382b209b371f56b1593145001",
        "06-bike.jpg": "f4b385d21491243a27352e2c3c2c2f8dc9c429705c670dbc4c460e040692986a",
        "07-shrimp.jpg": "d13cda59b52db1dc7436a64f74a3564817a6a88cbe80d5a9f1a1b80e52192e21",
    }
    html = (CASE / "index.html").read_text(encoding="utf-8")
    for name, digest in expected.items():
        assert hashlib.sha256((CASE / "assets" / name).read_bytes()).hexdigest() == digest
        assert html.count(f'src="assets/{name}"') == 1


def test_0912_follows_605_palette_and_confirmed_copy_only() -> None:
    index = (CASE / "index.html").read_text(encoding="utf-8")
    article = (CASE / "article.html").read_text(encoding="utf-8")
    cover = (CASE / "cover.html").read_text(encoding="utf-8")

    assert index == article
    assert "#a6dbf5" in index and "#a6dbf5" in cover
    assert "screen:nth-child" not in index
    assert "#f7edda" not in index + cover
    assert "#f9b43a" not in index + cover
    assert "北京自行车联赛" not in index + cover
    assert "DAILY" not in cover
    assert "辛庄桥小火车" in cover
    assert "font-size:54px" not in index


def test_0912_has_thumbnail_readable_type_contract() -> None:
    index = (CASE / "index.html").read_text(encoding="utf-8")
    cover = (CASE / "cover.html").read_text(encoding="utf-8")

    assert ".hero{font-size:92px" in index
    assert ".lead{font-size:44px" in index
    assert ".kicker{" in index and "font-size:32px" in index
    assert "-webkit-text-stroke:3px #101820" in index
    assert "paint-order:stroke fill" in index
    assert "text-shadow:8px 8px 0 #101820" in index
    assert ".title{" in cover and "font-size:110px" in cover
    assert ".sub{font-size:34px" in cover
    for color in ("#eaff00", "#fff", "#ff5fb0", "#25f4ee"):
        assert color in index
    assert ".blue{color:#755cff}" not in index


def test_0912_keeps_multi_person_photos_complete_without_fake_sidecars() -> None:
    body = (CASE / "index.html").read_text(encoding="utf-8")
    for name in (
        "01-podium.jpg",
        "02-race.jpg",
        "03-finish.jpg",
        "04-group-podium.jpg",
        "05-group-bike.jpg",
        "07-shrimp.jpg",
    ):
        line = next(line for line in body.splitlines() if f'src="assets/{name}"' in line)
        assert 'data-subject="none"' in line
        assert "data-subject-meta" not in line
        assert not (CASE / "assets" / f"{name}.subject.json").exists()
        assert not (CASE / "assets" / f"{name}.mask.png").exists()

    assert "full-image-fallback" not in body
