"""
Tests unitarios para mobiliti_saas/cliente/updater.py
"""
import sys
import os

# Add client dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mobiliti_saas", "cliente"))

from pathlib import Path
import updater


def test_parse_version_basic():
    assert updater._parse_version("1.5.3") == (1, 5, 3)
    assert updater._parse_version("2.0.0") == (2, 0, 0)
    assert updater._parse_version("0.0.1") == (0, 0, 1)


def test_parse_version_with_v_prefix():
    assert updater._parse_version("v1.5.3") == (1, 5, 3)
    assert updater._parse_version("V2.0.0") == (2, 0, 0)


def test_parse_version_short():
    assert updater._parse_version("1.5") == (1, 5, 0)
    assert updater._parse_version("1") == (1, 0, 0)


def test_parse_version_with_garbage():
    assert updater._parse_version("1.5.alpha") == (1, 5, 0)


def test_compare_version_less():
    assert updater._compare_version("1.5.2", "1.5.3") == -1
    assert updater._compare_version("1.4.9", "1.5.0") == -1


def test_compare_version_equal():
    assert updater._compare_version("1.5.3", "1.5.3") == 0
    assert updater._compare_version("2.0.0", "2.0.0") == 0


def test_compare_version_greater():
    assert updater._compare_version("1.6.0", "1.5.3") == 1
    assert updater._compare_version("2.0.0", "1.9.9") == 1


def test_compare_version_major_bump():
    assert updater._compare_version("2.0.0", "1.99.99") == 1


def test_get_resource_path_frozen(monkeypatch, tmp_path):
    """Simulate frozen exe scenario."""
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / "app.exe"), raising=False)
    monkeypatch.delattr(sys, '_MEIPASS', raising=False)

    # Create file in exe dir
    (tmp_path / "version.txt").write_text("1.2.3")
    path = updater._get_resource_path("version.txt")
    assert os.path.exists(path)
    assert Path(path).name == "version.txt"


def test_read_local_version(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, '_get_base_dir', lambda: str(tmp_path))
    (tmp_path / "version.txt").write_text("1.2.3\n", encoding="utf-8")
    assert updater._read_local_version() == "1.2.3"


def test_read_local_version_missing(monkeypatch):
    monkeypatch.setattr(updater, '_get_base_dir', lambda: "/nonexistent")
    assert updater._read_local_version() == "0.0.0"
