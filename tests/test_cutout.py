from __future__ import annotations

import sys
from types import SimpleNamespace

from PIL import Image

from xzq.renderer.cutout import (
    circular_avatar,
    cutout,
    outlined_cutout,
    prepare_subject_asset,
)


def test_cutout_cache_key_distinguishes_same_stem_and_extension(tmp_path, monkeypatch):
    jpg = tmp_path / "a" / "same.jpg"
    png = tmp_path / "b" / "same.png"
    jpg.parent.mkdir()
    png.parent.mkdir()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(jpg)
    Image.new("RGBA", (2, 2), (4, 5, 6, 255)).save(png)
    monkeypatch.setitem(
        sys.modules, "rembg", SimpleNamespace(remove=lambda image: image)
    )

    first = cutout(jpg, tmp_path / "assets")
    second = cutout(png, tmp_path / "assets")
    assert first and second and first != second


def test_fully_transparent_cutout_returns_none(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGBA", (2, 2), (1, 2, 3, 255)).save(source)
    transparent = Image.new("RGBA", (2, 2), (10, 20, 30, 0))
    monkeypatch.setitem(
        sys.modules, "rembg", SimpleNamespace(remove=lambda image: transparent)
    )

    assert cutout(source, tmp_path / "assets") is None
    assert not list((tmp_path / "assets").glob("*-cut.png"))


def test_existing_transparent_cutout_is_trimmed_without_rembg(tmp_path, monkeypatch):
    source = tmp_path / "person.png"
    image = Image.new("RGBA", (10, 12), (0, 0, 0, 0))
    for x in range(3, 8):
        for y in range(2, 10):
            image.putpixel((x, y), (20, 40, 60, 255))
    image.save(source)
    monkeypatch.setitem(sys.modules, "rembg", None)

    result = cutout(source, tmp_path / "assets")

    assert result is not None
    with Image.open(result) as trimmed:
        assert trimmed.size == (5, 8)
        assert trimmed.mode == "RGBA"


def test_outlined_cutout_adds_white_then_black_sticker_border(tmp_path):
    source = tmp_path / "person.png"
    image = Image.new("RGBA", (9, 9), (0, 0, 0, 0))
    image.putpixel((4, 4), (200, 80, 40, 255))
    image.save(source)

    result = outlined_cutout(source, tmp_path / "assets", white_px=2, black_px=1)

    assert result is not None
    with Image.open(result).convert("RGBA") as sticker:
        assert sticker.size == (7, 7)
        assert sticker.getpixel((3, 3)) == (200, 80, 40, 255)
        assert sticker.getpixel((3, 1))[:3] == (255, 255, 255)
        assert sticker.getpixel((3, 0))[:3] == (8, 8, 8)


def test_circular_avatar_crops_center_and_adds_white_black_rings(tmp_path):
    source = tmp_path / "portrait.png"
    Image.new("RGB", (12, 8), (20, 120, 200)).save(source)

    result = circular_avatar(source, tmp_path / "assets", size=20, white_px=3, black_px=2)

    assert result is not None
    with Image.open(result).convert("RGBA") as avatar:
        assert avatar.size == (20, 20)
        assert avatar.getpixel((0, 0))[3] == 0
        assert avatar.getpixel((10, 0))[:3] == (8, 8, 8)
        assert avatar.getpixel((10, 2))[:3] == (255, 255, 255)
        assert avatar.getpixel((10, 10))[:3] == (20, 120, 200)


def test_prepare_subject_asset_keeps_complete_cutout_and_writes_sidecar(
    tmp_path, monkeypatch
):
    source = tmp_path / "person.png"
    image = Image.new("RGBA", (20, 30), (0, 0, 0, 0))
    for x in range(4, 16):
        for y in range(2, 28):
            image.putpixel((x, y), (20, 120, 200, 255))
    image.save(source)
    monkeypatch.setattr("xzq.renderer.cutout.faces", lambda path: ((0.2, 0.1, 0.3, 0.3),))

    result = prepare_subject_asset(source, tmp_path / "assets")

    assert result.mode == "cutout"
    assert result.rendered.name.endswith("-outlined.png")
    assert result.sidecar.is_file()
    assert result.zone != "0,0,100,100"


def test_prepare_subject_asset_falls_back_when_cutout_loses_a_face(
    tmp_path, monkeypatch
):
    source = tmp_path / "group.png"
    Image.new("RGB", (40, 30), (20, 120, 200)).save(source)
    calls = iter([((0.1, 0.1, 0.2, 0.2), (0.6, 0.1, 0.2, 0.2)), ((0.1, 0.1, 0.2, 0.2),)])
    monkeypatch.setattr("xzq.renderer.cutout.faces", lambda path: next(calls))
    cut = tmp_path / "assets" / "fake-cut.png"
    cut.parent.mkdir()
    Image.new("RGBA", (20, 20), (1, 2, 3, 255)).save(cut)
    outlined = tmp_path / "assets" / "fake-outlined.png"
    Image.new("RGBA", (24, 24), (1, 2, 3, 255)).save(outlined)
    monkeypatch.setattr("xzq.renderer.cutout.cutout", lambda src, dst: cut)
    monkeypatch.setattr("xzq.renderer.cutout._outline_existing", lambda *args, **kwargs: outlined)

    result = prepare_subject_asset(source, tmp_path / "assets")

    assert result.mode == "full-image-fallback"
    assert result.rendered.name == "group.png"
    assert result.zone == "0,0,100,100"
    payload = __import__("json").loads(result.sidecar.read_text(encoding="utf-8"))
    assert payload["fallback_reason"] == "cutout-lost-faces"
