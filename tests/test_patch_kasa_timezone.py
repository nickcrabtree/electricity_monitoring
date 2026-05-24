"""Tests for tools/patch_kasa_timezone: is_patched and apply_patch."""

import sys
import pytest
from pathlib import Path

# tools/ is not on the default path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import patch_kasa_timezone as pkt


class TestIsPatched:
    def test_detects_marker_present(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text("some code\n_TZ_ALIASES = {}\nmore code\n")
        assert pkt.is_patched(f) is True

    def test_detects_marker_absent(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text("original content without the marker\n")
        assert pkt.is_patched(f) is False

    def test_empty_file_returns_false(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text("")
        assert pkt.is_patched(f) is False


class TestApplyPatch:
    def test_applies_patch_to_unpatched_file(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text("original content\n")
        result = pkt.apply_patch(f)
        assert result is True
        assert pkt.PATCH_MARKER in f.read_text()

    def test_creates_backup_on_apply(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text("original content\n")
        pkt.apply_patch(f)
        backups = list(tmp_path.glob("cachedzoneinfo.py.*.bak"))
        assert len(backups) == 1

    def test_skips_already_patched_file(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text(pkt.PATCHED_CONTENT)
        result = pkt.apply_patch(f)
        assert result is False

    def test_force_reapplies_already_patched(self, tmp_path):
        f = tmp_path / "cachedzoneinfo.py"
        f.write_text(pkt.PATCHED_CONTENT)
        result = pkt.apply_patch(f, force=True)
        assert result is True
