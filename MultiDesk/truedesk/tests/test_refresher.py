# -*- coding: utf-8 -*-
"""refresher 策略测试：verify 通过不重启、失败自动重启、none 不动作。"""
from truedesk import refresher


def test_none_does_nothing():
    refreshed, restarted = refresher.refresh("none", restart_allowed=True)
    assert refreshed is False
    assert restarted is False


def test_auto_verify_pass_no_restart(monkeypatch):
    called = {"restart": 0}

    def fake_restart(*a, **k):
        called["restart"] += 1
        return True

    monkeypatch.setattr(refresher, "restart_explorer", fake_restart)
    refreshed, restarted = refresher.refresh("auto", verify=lambda: True, timeout=0.5)
    assert refreshed is True
    assert restarted is False
    assert called["restart"] == 0


def test_auto_verify_fail_restarts(monkeypatch):
    called = {"restart": 0}

    def fake_restart(*a, **k):
        called["restart"] += 1
        return True

    monkeypatch.setattr(refresher, "restart_explorer", fake_restart)
    refreshed, restarted = refresher.refresh("auto", verify=lambda: False, timeout=0.5)
    assert refreshed is True  # 重启后视为已生效（注册表已指向目标）
    assert restarted is True
    assert called["restart"] == 1


def test_auto_verify_fail_restart_blocked(monkeypatch):
    called = {"restart": 0}

    def fake_restart(*a, **k):
        called["restart"] += 1
        return True

    monkeypatch.setattr(refresher, "restart_explorer", fake_restart)
    refreshed, restarted = refresher.refresh("auto", verify=lambda: False,
                                             timeout=0.5, restart_allowed=False)
    assert refreshed is False
    assert restarted is False
    assert called["restart"] == 0


def test_restart_mode_directly_restarts(monkeypatch):
    called = {"restart": 0}

    def fake_restart(*a, **k):
        called["restart"] += 1
        return True

    monkeypatch.setattr(refresher, "restart_explorer", fake_restart)
    refreshed, restarted = refresher.refresh("restart")
    assert restarted is True
    assert called["restart"] == 1
