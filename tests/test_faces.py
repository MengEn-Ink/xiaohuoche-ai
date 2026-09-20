"""人脸框只决定字往哪躲：没法检测 → 不压图；上带没脸 → 压上；都有脸 → 不压。"""

from xzq.renderer.faces import corner_free, free_band


def test_free_band_prefers_top_then_bottom_then_none():
    assert free_band(None) is None  # 缺模型：不知道脸在哪就不压
    assert free_band(()) == "top"  # 风景照
    assert free_band(((0.4, 0.1, 0.1, 0.15),)) == "bottom"  # 脸在上面
    assert free_band(((0.4, 0.1, 0.1, 0.15), (0.5, 0.7, 0.1, 0.15))) is None


def test_corner_free_skips_faces_and_defaults_right_side():
    boxes = ((0.8, 0.05, 0.1, 0.15),)  # 右上角有脸
    assert not corner_free(boxes, "tr")
    assert corner_free(boxes, "bl")
    assert corner_free(None, "mr") and not corner_free(None, "tl")
