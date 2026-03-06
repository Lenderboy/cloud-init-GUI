"""
Tests for iso_builder.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
from pathlib import Path

import pytest
import pycdlib

from iso_builder import build_seed_iso


USER_DATA = "#cloud-config\nhostname: test-host\n"
META_DATA = "instance-id: test-id\nlocal-hostname: test-host\n"
NET_CFG = "version: 2\nethernets:\n  eth0:\n    dhcp4: true\n"


class TestBuildSeedIso:
    def _read_joliet(self, iso_path: Path, joliet_path: str) -> bytes:
        iso = pycdlib.PyCdlib()
        iso.open(str(iso_path))
        outfp = io.BytesIO()
        iso.get_file_from_iso_fp(outfp, joliet_path=joliet_path)
        iso.close()
        return outfp.getvalue()

    def test_creates_iso_file(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_iso_has_user_data(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)
        assert self._read_joliet(out, "/user-data").decode() == USER_DATA

    def test_iso_has_meta_data(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)
        assert self._read_joliet(out, "/meta-data").decode() == META_DATA

    def test_iso_without_network_config(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)

        iso = pycdlib.PyCdlib()
        iso.open(str(out))
        with pytest.raises(pycdlib.pycdlibexception.PyCdlibInvalidInput):
            iso.get_file_from_iso_fp(io.BytesIO(), joliet_path="/network-config")
        iso.close()

    def test_iso_with_network_config(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out, network_config=NET_CFG)
        assert self._read_joliet(out, "/network-config").decode() == NET_CFG

    def test_returns_path_object(self, tmp_path):
        out = tmp_path / "seed.iso"
        result = build_seed_iso(USER_DATA, META_DATA, out)
        assert isinstance(result, Path)
        assert result == out

    def test_creates_parent_directories(self, tmp_path):
        out = tmp_path / "sub" / "dir" / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)
        assert out.exists()

    def test_volume_label_is_cidata(self, tmp_path):
        out = tmp_path / "seed.iso"
        build_seed_iso(USER_DATA, META_DATA, out)

        iso = pycdlib.PyCdlib()
        iso.open(str(out))
        pvd = iso.pvd
        assert pvd.volume_identifier.decode().strip().upper() == "CIDATA"
        iso.close()
