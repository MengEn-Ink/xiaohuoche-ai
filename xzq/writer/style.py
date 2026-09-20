"""风格资产加载：从 style/ 目录读取写作说明书、黑话词典、栏目模板、few-shot。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_STYLE_DIR = "style"


@dataclass
class StyleAssets:
    style_dir: Path
    writing_style: str = ""
    slang: str = ""
    templates: dict[str, str] = field(default_factory=dict)
    few_shots: list[tuple[str, str]] = field(default_factory=list)
    few_shot_names: list[str] = field(
        default_factory=list
    )  # 与 few_shots 同序，形如 20240605-ribao

    @classmethod
    def load(cls, style_dir: str | Path = DEFAULT_STYLE_DIR) -> "StyleAssets":
        base = Path(style_dir)
        assets = cls(style_dir=base)
        assets.writing_style = _read(base / "writing_style.md")
        assets.slang = _read(base / "slang.md")
        tpl_dir = base / "templates"
        if tpl_dir.is_dir():
            assets.templates = {
                p.stem: p.read_text(encoding="utf-8") for p in tpl_dir.glob("*.md")
            }
        fs_dir = base / "few_shot"
        if fs_dir.is_dir():
            # 同名 .input.md / .output.json 配对成一个 few-shot
            # 下划线开头的样例不喂给模型（如作者复刻版 _20260908：留作参考，但它不是小编原声）
            inputs = {
                p.name.split(".")[0]: p
                for p in fs_dir.glob("*.input.md")
                if not p.name.startswith("_")
            }
            for key, ip in sorted(inputs.items()):
                op = fs_dir / f"{key}.output.json"
                if op.exists():
                    assets.few_shots.append(
                        (ip.read_text(encoding="utf-8"), op.read_text(encoding="utf-8"))
                    )
                    assets.few_shot_names.append(key)
        return assets

    def template_for(self, kind: str) -> str:
        """取栏目模板；缺失时给空串（写作层据此判断是否离线兜底）。"""
        return self.templates.get(kind, "")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""
