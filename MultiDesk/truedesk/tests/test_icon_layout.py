# -*- coding: utf-8 -*-
"""icon_layout 序列化与容错测试（不依赖真实桌面窗口）。"""
from truedesk.icon_layout import apply_layout, save_layout


def test_save_layout_without_desktop_is_safe():
    # 无桌面窗口环境下：不抛异常，返回 saved=False
    layout = save_layout()
    assert isinstance(layout, dict)
    assert "saved" in layout
    assert "items" in layout


def test_save_layout_pinned_flag_passthrough():
    # pinned 标记必须透传，用于区分手动基准布局与切换时自动保存
    assert save_layout(pinned=True).get("pinned") is True
    assert save_layout(pinned=False).get("pinned") is False
    # 默认（切换时自动保存）不得标记为 pinned
    assert save_layout().get("pinned") is False


def test_apply_layout_empty_is_noop():
    assert apply_layout(None) == 0
    assert apply_layout({}) == 0
    assert apply_layout({"saved": False}) == 0
    assert apply_layout({"saved": True, "items": []}) == 0


def test_layout_structure_roundtrip():
    layout = {
        "saved": True,
        "items": [{"name": "文件.txt", "x": 10, "y": 20}],
        "count": 1,
        "pinned": True,
    }
    # 序列化 JSON 往返
    import json
    again = json.loads(json.dumps(layout))
    assert again["items"][0]["name"] == "文件.txt"
    assert again["items"][0]["x"] == 10
    assert again["pinned"] is True
