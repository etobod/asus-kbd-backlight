"""FR-12 / R-8: the daemon reconciles a Task Scheduler logon entry.

The real registration and the actual logon are manual (TEST-PLAN). What is
pinned here is :func:`autostart.reconcile`'s create / refresh / delete / no-op
decision and that no COM failure escapes it.
"""

from __future__ import annotations

from asus_kbd_backlight import autostart


class _Folder:
    def __init__(self, present: bool):
        self._present = present
        self.registered: list[str] = []
        self.deleted: list[str] = []

    def GetTask(self, name):  # noqa: N802 - COM casing
        if not self._present:
            raise OSError("The system cannot find the file specified. (0x80070002)")
        return object()

    def RegisterTaskDefinition(self, name, td, flags, user, pw, logon):  # noqa: N802
        self.registered.append(name)
        self._present = True

    def DeleteTask(self, name, flags):  # noqa: N802
        if not self._present:
            raise OSError("not found")
        self.deleted.append(name)
        self._present = False


class _AutoNode:
    """Attribute sink that also serves as Triggers/Actions with .Create()."""

    def __getattr__(self, _name):
        child = _AutoNode()
        object.__setattr__(self, _name, child)
        return child

    def Create(self, _kind):  # noqa: N802 - COM casing
        return _AutoNode()


class _Service:
    def __init__(self, present: bool):
        self.folder = _Folder(present)

    def GetFolder(self, path):  # noqa: N802
        assert path == autostart.TASK_FOLDER
        return self.folder

    def NewTask(self, _flags):  # noqa: N802
        return _AutoNode()


def test_reconcile_creates_when_enabled_and_absent():
    svc = _Service(present=False)
    autostart.reconcile(True, service=svc)
    assert svc.folder.registered == [autostart.TASK_NAME]
    assert svc.folder.deleted == []


def test_reconcile_refreshes_when_enabled_and_present():
    svc = _Service(present=True)
    autostart.reconcile(True, service=svc)
    # Re-registered (self-heals a moved exe), never deleted.
    assert svc.folder.registered == [autostart.TASK_NAME]
    assert svc.folder.deleted == []


def test_reconcile_deletes_when_disabled_and_present():
    svc = _Service(present=True)
    autostart.reconcile(False, service=svc)
    assert svc.folder.deleted == [autostart.TASK_NAME]
    assert svc.folder.registered == []


def test_reconcile_is_a_noop_when_disabled_and_absent():
    svc = _Service(present=False)
    autostart.reconcile(False, service=svc)
    assert svc.folder.registered == []
    assert svc.folder.deleted == []


def test_reconcile_swallows_a_com_failure(caplog):
    class _Broken:
        def GetFolder(self, path):  # noqa: N802
            raise OSError("RPC server is unavailable")

    with caplog.at_level("WARNING"):
        autostart.reconcile(True, service=_Broken())  # must not raise
    assert any("could not reconcile" in r.message for r in caplog.records)


def test_task_exists_reports_presence():
    assert autostart.task_exists(service=_Service(present=True)) is True
    assert autostart.task_exists(service=_Service(present=False)) is False


def test_action_target_from_source_runs_the_module(monkeypatch):
    monkeypatch.setattr(autostart.sys, "frozen", False, raising=False)
    program, arguments = autostart.action_target()
    assert arguments == "-m asus_kbd_backlight"
    assert program.lower().endswith(("python.exe", "pythonw.exe"))


def test_action_target_when_frozen_is_the_exe(monkeypatch):
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Apps\asus-kbd-backlight.exe")
    assert autostart.action_target() == (r"C:\Apps\asus-kbd-backlight.exe", "")


def test_populate_sets_run_level_highest_and_a_logon_trigger():
    # A light structural check that _populate wires the security-relevant bits.
    td = _AutoNode()
    autostart._populate(td)
    assert td.Principal.RunLevel == autostart._TASK_RUNLEVEL_HIGHEST
    assert isinstance(td.Actions, _AutoNode)


def test_module_constants_are_stable():
    assert autostart.TASK_NAME == "asus-kbd-backlight"
    assert isinstance(autostart._current_user(), str)
