"""FR-12 / R-8: the daemon reconciles a Task Scheduler logon entry.

The real registration and the actual logon are manual (TEST-PLAN). What is
pinned here is :func:`autostart.reconcile`'s create / refresh / delete / no-op
decision and that no COM failure escapes it.
"""

from __future__ import annotations

from types import SimpleNamespace

from asus_kbd_backlight import autostart


class _Tasks:
    """IRegisteredTaskCollection stand-in: 1-based ``Item``, ``Count``."""

    def __init__(self, names):
        self._names = list(names)

    @property
    def Count(self):  # noqa: N802 - COM casing
        return len(self._names)

    def Item(self, index):  # noqa: N802
        return SimpleNamespace(Name=self._names[index - 1])


class _Folder:
    def __init__(self, present: bool, *, listing_error: Exception | None = None):
        self._present = present
        self._listing_error = listing_error
        self.registered: list[str] = []
        self.deleted: list[str] = []

    def GetTasks(self, flags):  # noqa: N802 - COM casing
        assert flags == autostart._TASK_ENUM_HIDDEN
        if self._listing_error is not None:
            raise self._listing_error
        others = ["OneDrive Standalone Update Task", "MicrosoftEdgeUpdateTaskMachineCore"]
        return _Tasks([*others, autostart.TASK_NAME] if self._present else others)

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
    def __init__(self, present: bool, **folder_kwargs):
        self.folder = _Folder(present, **folder_kwargs)

    def GetFolder(self, path):  # noqa: N802
        assert path == autostart.TASK_FOLDER
        return self.folder

    def NewTask(self, _flags):  # noqa: N802
        return _AutoNode()


def test_task_exists_is_decided_by_the_listing():
    assert autostart._task_exists(_Folder(present=True)) is True
    assert autostart._task_exists(_Folder(present=False)) is False


def test_task_names_match_case_insensitively():
    folder = _Folder(present=False)
    folder.GetTasks = lambda flags: _Tasks(["ASUS-KBD-Backlight"])
    assert autostart._task_exists(folder) is True


def test_reconcile_reports_a_refused_listing_instead_of_skipping_the_delete():
    # If the folder can't be listed, "absent" would skip the delete and return
    # success while the logon task stays registered. It must surface as a failure.
    svc = _Service(present=True, listing_error=OSError("Access is denied."))
    assert autostart.reconcile(False, service=svc) is False
    assert svc.folder.deleted == []


def test_populate_runs_the_task_at_normal_priority():
    # Task Scheduler's default (7, below normal) can starve the keyboard hook.
    td = _AutoNode()
    autostart._populate(td)
    assert td.Settings.Priority == 4


def test_reconcile_creates_when_enabled_and_absent():
    svc = _Service(present=False)
    assert autostart.reconcile(True, service=svc) is True
    assert svc.folder.registered == [autostart.TASK_NAME]
    assert svc.folder.deleted == []


def test_reconcile_refreshes_when_enabled_and_present():
    svc = _Service(present=True)
    assert autostart.reconcile(True, service=svc) is True
    # Re-registered (self-heals a moved exe), never deleted.
    assert svc.folder.registered == [autostart.TASK_NAME]
    assert svc.folder.deleted == []


def test_reconcile_deletes_when_disabled_and_present():
    svc = _Service(present=True)
    assert autostart.reconcile(False, service=svc) is True
    assert svc.folder.deleted == [autostart.TASK_NAME]
    assert svc.folder.registered == []


def test_reconcile_is_a_quiet_noop_when_disabled_and_absent():
    svc = _Service(present=False)
    assert autostart.reconcile(False, service=svc) is True
    assert svc.folder.registered == []
    assert svc.folder.deleted == []


def test_reconcile_swallows_a_com_failure(caplog):
    class _Broken:
        def GetFolder(self, path):  # noqa: N802
            raise OSError("RPC server is unavailable")

    with caplog.at_level("WARNING"):
        assert autostart.reconcile(True, service=_Broken()) is False  # must not raise
    assert any("could not reconcile" in r.message for r in caplog.records)


def test_task_exists_reports_presence():
    assert autostart.task_exists(service=_Service(present=True)) is True
    assert autostart.task_exists(service=_Service(present=False)) is False


def test_action_target_from_source_runs_the_module(fake_exe):
    fake_exe("python.exe", "pythonw.exe")
    program, arguments = autostart.action_target()
    assert arguments == "-m asus_kbd_backlight"
    assert program.endswith("pythonw.exe")  # no console flash at logon


def test_action_target_when_frozen_is_the_exe(fake_exe):
    tmp = fake_exe("asus-kbd-backlight.exe", frozen=True)
    assert autostart.action_target() == (str(tmp / "asus-kbd-backlight.exe"), "")


def test_action_target_prefers_the_windowed_exe_over_the_debug_build(fake_exe):
    # A daemon started from the console debug build must still register the
    # windowed exe, or a console window would open at every logon.
    tmp = fake_exe("asus-kbd-backlight-debug.exe", "asus-kbd-backlight.exe", frozen=True)
    assert autostart.action_target() == (str(tmp / "asus-kbd-backlight.exe"), "")


def test_populate_sets_run_level_highest_and_a_logon_trigger():
    # A light structural check that _populate wires the security-relevant bits.
    td = _AutoNode()
    autostart._populate(td)
    assert td.Principal.RunLevel == autostart._TASK_RUNLEVEL_HIGHEST
    assert isinstance(td.Actions, _AutoNode)


def test_module_constants_are_stable():
    assert autostart.TASK_NAME == "asus-kbd-backlight"
    assert isinstance(autostart._current_user(), str)
