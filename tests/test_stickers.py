"""贴纸库：按名/按用途找得到，visual 里的「贴纸：」解析正确，梗图没装时静默。"""

from pathlib import Path

from xzq.renderer.stickers import StickerLib, STICKER_RE, MEME_RE

ROOT = Path(__file__).resolve().parent.parent


def test_library_loads_and_finds_by_key_name_or_use():
    lib = StickerLib(ROOT / "style")
    assert len(lib.items) >= 10
    assert lib.find("panda_shock").name == "panda_shock.png"
    assert lib.find("熊猫头惊").name == "panda_shock.png"
    assert lib.find("谴责").name == "gun_face.png"  # 按用途词
    assert lib.find("熊猫头").name in (
        "panda_shock.png",
        "panda_smoke.png",
    )  # 半个名字按片段找
    assert lib.find("不存在的") is None


def test_visual_parsing():
    assert STICKER_RE.findall("拿枪黄脸压右上角；贴纸：熊猫头惊。文字放底部") == [
        "熊猫头惊"
    ]
    assert STICKER_RE.findall("贴纸：猫开车（不放路线截图）") == [
        "猫开车"
    ]  # 括号说明不算名字
    m = MEME_RE.search("梗图：喜报|刀刀成为辛庄桥史铁生；不放照片")
    assert m and m.group(1) == "喜报" and m.group(2) == "刀刀成为辛庄桥史铁生"


def test_from_visual_returns_existing_files(tmp_path: Path):
    import shutil

    shutil.copytree(ROOT / "style" / "stickers", tmp_path / "style" / "stickers")
    lib = StickerLib(tmp_path / "style")
    assert lib.unknown("贴纸：王咪猫；贴纸：不存在的") == ["不存在的"]
    paths = lib.from_visual("贴纸：王咪猫；贴纸：不存在的")
    assert paths[0].name == "cat_wangmi.png"
    # 库里没有的名字：有 meme-generator 就拿那几个字做举牌梗图，没有就跳过——两种都不报错
    assert len(paths) in (1, 2)


def test_menu_mentions_keys_and_templates():
    menu = StickerLib(ROOT / "style").menu()
    assert "拿枪黄脸" in menu and "喜报" in menu
    assert menu.count("\n") == 0  # 压成一行，别把 prompt 撑长


def test_generated_item_is_not_matched_by_ordinary_fuzzy_search(tmp_path: Path):
    import json

    sticker_dir = tmp_path / "style" / "stickers"
    generated = sticker_dir / "generated" / "made.png"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"png")
    (sticker_dir / "stickers.json").write_text(
        json.dumps(
            {
                "stickers": [
                    {
                        "key": "made",
                        "name": "喜报：独特文字",
                        "file": "generated/made.png",
                        "use": "生成过的梗图，同模板同文字直接复用",
                        "generated": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lib = StickerLib(tmp_path / "style")
    assert lib.find("生成过") is None
    assert lib.find("made") == generated


def test_manifest_updates_are_thread_safe_and_atomic(tmp_path: Path, monkeypatch):
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    sticker_dir = tmp_path / "style" / "stickers"
    sticker_dir.mkdir(parents=True)
    (sticker_dir / "stickers.json").write_text(
        json.dumps({"_doc": "测试", "stickers": []}), encoding="utf-8"
    )

    params = SimpleNamespace(min_images=0, min_texts=1, max_texts=1)
    meme = SimpleNamespace(
        info=SimpleNamespace(params=params),
        generate=lambda images, texts, options: b"png",
    )
    monkeypatch.setitem(
        sys.modules, "meme_generator", SimpleNamespace(get_meme=lambda key: meme)
    )
    libs = [StickerLib(tmp_path / "style"), StickerLib(tmp_path / "style")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(
            pool.map(
                lambda pair: pair[0].meme("喜报", pair[1]), zip(libs, ("甲", "乙"))
            )
        )

    manifest = json.loads((sticker_dir / "stickers.json").read_text(encoding="utf-8"))
    assert all(path and path.is_file() for path in paths)
    assert {item["name"] for item in manifest["stickers"]} == {"喜报：甲", "喜报：乙"}
    assert not list(sticker_dir.glob("*.tmp"))
