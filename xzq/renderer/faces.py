"""人脸框：只用来决定大字和贴纸往哪边躲，不做任何识别。

opencv 自带的 YuNet 检测器，模型 ~230KB 由 `pipeline.py setup` 下到 ~/.xhc/yunet.onnx。
模型没下、cv2 没装、图读不出来都返回「没脸」；调用方拿到 None 的空闲带就把字放到照片外面，
所以缺模型不会把字压到人脸上，只会少一种排版。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

MODEL = Path(os.getenv("XHC_FACE_MODEL", str(Path.home() / ".xhc" / "yunet.onnx")))
MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

Box = tuple[float, float, float, float]  # 归一化 x, y, w, h（0-1）


@lru_cache(maxsize=256)
def faces(path: str) -> tuple[Box, ...] | None:
    """None = 没法检测（缺模型/缺 cv2/图读不出）；空元组 = 检测了但没脸。两者调用方处理不同。"""
    if not MODEL.exists():
        return None
    try:
        import cv2
    except ImportError:
        return None
    try:
        img = cv2.imread(str(path))
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = min(1.0, 1280 / max(h, w))
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
            h, w = img.shape[:2]
        det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.6)
        _, found = det.detect(img)
        if found is None:
            return ()
        return tuple(
            (float(x / w), float(y / h), float(bw / w), float(bh / h))
            for x, y, bw, bh in found[:, :4]
        )
    except Exception:
        return None


def _hit(
    boxes: tuple[Box, ...],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    margin: float = 0.05,
) -> bool:
    return any(
        x - margin < x1
        and x + w + margin > x0
        and y - margin < y1
        and y + h + margin > y0
        for x, y, w, h in boxes
    )


def free_band(boxes: tuple[Box, ...] | None) -> str | None:
    """照片上/下各 42% 高的横带，哪条没脸字就压哪条；都有脸返回 None。
    boxes 为 None 表示根本没检测（缺模型），同样返回 None——不知道脸在哪就不压。"""
    if boxes is None:
        return None
    if not _hit(boxes, 0, 0, 1, 0.42):
        return "top"
    if not _hit(boxes, 0, 0.58, 1, 1):
        return "bottom"
    return None


# 贴纸落点 → 在照片坐标里占的角落（贴纸约 250px，照片约 1000px 宽）
_CORNER = {
    "tl": (0, 0, 0.3, 0.35),
    "tr": (0.7, 0, 1, 0.35),
    "bl": (0, 0.65, 0.3, 1),
    "br": (0.7, 0.65, 1, 1),
    "mr": (0.7, 0.3, 1, 0.7),
    "ml": (0, 0.3, 0.3, 0.7),
}


def corner_free(boxes: tuple[Box, ...] | None, corner: str) -> bool:
    if boxes is None:
        return corner in ("tr", "br", "mr")  # 不知道脸在哪：只用不压照片主体的右侧
    return not _hit(boxes, *_CORNER[corner])
