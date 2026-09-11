import textwrap

import pytest

from asus_kbd_backlight import config


def test_defaults_when_file_missing(tmp_path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.timeout == config.DEFAULT_TIMEOUT
    assert cfg.on_level == config.DEFAULT_ON_LEVEL
    assert cfg.device_id == config.DEFAULT_DEVICE_ID


def test_overrides_from_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent(
            """
            timeout = 5.0
            on_level = 2
            device_id = "0x00050021"
            """
        )
    )
    cfg = config.load(path)
    assert cfg.timeout == 5.0
    assert cfg.on_level == 2
    assert cfg.device_id == 0x00050021


def test_device_id_accepts_int(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("device_id = 327713\n")  # 0x00050021
    assert config.load(path).device_id == 0x00050021


@pytest.mark.parametrize("body", ["timeout = 0", "timeout = -1", "on_level = 0", "on_level = 4"])
def test_invalid_values_rejected(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body + "\n")
    with pytest.raises(ValueError):
        config.load(path)


def test_default_path_uses_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    p = config.default_config_path()
    assert p.name == "config.toml"
    assert p.parent.name == "asus-kbd-backlight"


def test_device_id_bare_hex_without_0x(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('device_id = "00050021"\n')  # zero-padded, no 0x, meant as hex
    assert config.load(path).device_id == 0x00050021


def test_device_id_garbage_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('device_id = "not-a-number"\n')
    with pytest.raises(ValueError, match="device_id"):
        config.load(path)


def test_timeout_non_numeric_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timeout = "soon"\n')
    with pytest.raises(ValueError, match="timeout"):
        config.load(path)


def test_on_level_float_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("on_level = 2.9\n")
    with pytest.raises(ValueError, match="on_level"):
        config.load(path)


def test_unknown_key_warns_but_loads(tmp_path, caplog):
    path = tmp_path / "config.toml"
    path.write_text("timeout = 4\nfrobnicate = true\n")
    with caplog.at_level("WARNING"):
        cfg = config.load(path)
    assert cfg.timeout == 4.0
    assert any("frobnicate" in r.message for r in caplog.records)


def test_autostart_default_is_true(tmp_path):
    assert config.load(tmp_path / "nope.toml").autostart is True


@pytest.mark.parametrize("literal,expected", [("true", True), ("false", False)])
def test_autostart_override_from_file(tmp_path, literal, expected):
    path = tmp_path / "config.toml"
    path.write_text(f"autostart = {literal}\n")
    assert config.load(path).autostart is expected


def test_autostart_non_bool_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('autostart = "yes"\n')
    with pytest.raises(ValueError, match="autostart"):
        config.load(path)


@pytest.mark.parametrize(
    "raw",
    ['device_id = 0', 'device_id = -1', 'device_id = "0x100000000"', 'device_id = 4294967296'],
)
def test_device_id_out_of_range_rejected(tmp_path, raw):
    # validated(): 0 < device_id <= 0xFFFFFFFF (a 32-bit ACPI endpoint id).
    path = tmp_path / "config.toml"
    path.write_text(raw + "\n")
    with pytest.raises(ValueError, match="device_id"):
        config.load(path)


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    src = config.Config(timeout=12.5, on_level=3, device_id=0x00050021, autostart=False)
    written = config.save(src, path)
    assert written == path
    assert config.load(path) == src


def test_save_is_atomic_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=7.0), path)
    assert path.is_file()
    assert list(tmp_path.iterdir()) == [path]  # no *.tmp sibling left behind


def test_save_overwrites_in_place(tmp_path):
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=2.0), path)
    config.save(config.Config(timeout=9.0, on_level=2), path)
    reloaded = config.load(path)
    assert reloaded.timeout == 9.0
    assert reloaded.on_level == 2


def test_save_rejects_an_invalid_config(tmp_path):
    with pytest.raises(ValueError):
        config.save(config.Config(timeout=0), tmp_path / "config.toml")
    assert not list(tmp_path.iterdir())


def test_load_bytes_parses_and_validates():
    cfg = config.load_bytes(b"timeout = 8.0\non_level = 2\nautostart = false\n")
    assert cfg.timeout == 8.0
    assert cfg.on_level == 2
    assert cfg.autostart is False


def test_load_bytes_rejects_malformed_toml():
    with pytest.raises(ValueError):
        config.load_bytes(b"timeout = = 3\n")


def test_load_bytes_rejects_invalid_utf8():
    with pytest.raises(ValueError):
        config.load_bytes(b"timeout = 3 \xff\xfe\n")


def test_load_missing_file_still_yields_defaults(tmp_path):
    # load() keeps its "a missing file is not an error" contract after the
    # load_bytes split.
    assert config.load(tmp_path / "absent.toml") == config.Config()
