"""
Tests for ova_handler.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tarfile
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ova_handler import (
    _NS,
    apply_seed_iso_to_ova,
    extract_ova,
    find_ovf,
    inject_seed_iso_into_ovf,
    repack_ova,
)


# ---------------------------------------------------------------------------
# Minimal OVF fixture
# ---------------------------------------------------------------------------

MINIMAL_OVF = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <ovf:Envelope
        xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
        xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
        xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData">
      <ovf:References>
        <ovf:File ovf:href="disk.vmdk" ovf:id="disk_vmdk"/>
      </ovf:References>
      <ovf:VirtualSystem ovf:id="ubuntu">
        <ovf:VirtualHardwareSection>
          <ovf:Item>
            <rasd:InstanceID>1</rasd:InstanceID>
            <rasd:ResourceType>3</rasd:ResourceType>
            <rasd:VirtualQuantity>1</rasd:VirtualQuantity>
          </ovf:Item>
          <ovf:Item>
            <rasd:InstanceID>2</rasd:InstanceID>
            <rasd:ResourceType>5</rasd:ResourceType>
            <rasd:ElementName>IDE Controller 0</rasd:ElementName>
          </ovf:Item>
        </ovf:VirtualHardwareSection>
      </ovf:VirtualSystem>
    </ovf:Envelope>
""")


def _make_ova(dest: Path, extra_files: dict[str, bytes] | None = None) -> Path:
    """Create a minimal OVA tar at *dest* and return the path."""
    dest.mkdir(parents=True, exist_ok=True)
    ova_path = dest / "test.ova"
    with tarfile.open(str(ova_path), "w") as tar:
        # OVF
        ovf_bytes = MINIMAL_OVF.encode("utf-8")
        import io
        info = tarfile.TarInfo(name="test.ovf")
        info.size = len(ovf_bytes)
        tar.addfile(info, io.BytesIO(ovf_bytes))

        # Fake disk
        disk_bytes = b"\x00" * 512
        info = tarfile.TarInfo(name="disk.vmdk")
        info.size = len(disk_bytes)
        tar.addfile(info, io.BytesIO(disk_bytes))

        # Any extra files
        for name, data in (extra_files or {}).items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return ova_path


# ---------------------------------------------------------------------------
# extract_ova
# ---------------------------------------------------------------------------

class TestExtractOva:
    def test_extracts_files(self, tmp_path):
        ova = _make_ova(tmp_path / "src")
        dest = tmp_path / "out"
        extract_ova(ova, dest)
        assert (dest / "test.ovf").exists()
        assert (dest / "disk.vmdk").exists()

    def test_invalid_file_raises(self, tmp_path):
        bad = tmp_path / "bad.ova"
        bad.write_bytes(b"not a tar file")
        with pytest.raises(ValueError, match="valid OVA"):
            extract_ova(bad, tmp_path / "out")

    def test_path_traversal_blocked(self, tmp_path):
        import io
        ova_path = tmp_path / "bad.ova"
        with tarfile.open(str(ova_path), "w") as tar:
            data = b"evil"
            info = tarfile.TarInfo(name="../evil.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(ValueError, match="unsafe path"):
            extract_ova(ova_path, tmp_path / "out")


# ---------------------------------------------------------------------------
# find_ovf
# ---------------------------------------------------------------------------

class TestFindOvf:
    def test_finds_ovf(self, tmp_path):
        (tmp_path / "vm.ovf").write_text("x")
        result = find_ovf(tmp_path)
        assert result.name == "vm.ovf"

    def test_raises_when_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            find_ovf(tmp_path)

    def test_raises_when_multiple(self, tmp_path):
        (tmp_path / "a.ovf").write_text("x")
        (tmp_path / "b.ovf").write_text("x")
        with pytest.raises(ValueError, match="Multiple"):
            find_ovf(tmp_path)


# ---------------------------------------------------------------------------
# inject_seed_iso_into_ovf
# ---------------------------------------------------------------------------

class TestInjectSeedIsoIntoOvf:
    def _parse(self, path: Path) -> ET.Element:
        return ET.parse(str(path)).getroot()

    def test_adds_file_reference(self, tmp_path):
        ovf_path = tmp_path / "test.ovf"
        ovf_path.write_text(MINIMAL_OVF, encoding="utf-8")
        inject_seed_iso_into_ovf(ovf_path, "seed.iso")
        root = self._parse(ovf_path)
        refs = root.find(f"{{{_NS['ovf']}}}References")
        hrefs = [
            el.get(f"{{{_NS['ovf']}}}href")
            for el in refs.findall(f"{{{_NS['ovf']}}}File")
        ]
        assert "seed.iso" in hrefs

    def test_adds_cdrom_item(self, tmp_path):
        ovf_path = tmp_path / "test.ovf"
        ovf_path.write_text(MINIMAL_OVF, encoding="utf-8")
        inject_seed_iso_into_ovf(ovf_path, "seed.iso")
        root = self._parse(ovf_path)
        vhs = root.find(f".//{{{_NS['ovf']}}}VirtualHardwareSection")
        resource_types = [
            el.text
            for item in vhs.findall(f"{{{_NS['ovf']}}}Item")
            for el in item.findall(f"{{{_NS['rasd']}}}ResourceType")
        ]
        assert "15" in resource_types  # ResourceType 15 = CD/DVD Drive

    def test_idempotent_file_reference(self, tmp_path):
        ovf_path = tmp_path / "test.ovf"
        ovf_path.write_text(MINIMAL_OVF, encoding="utf-8")
        inject_seed_iso_into_ovf(ovf_path, "seed.iso")
        inject_seed_iso_into_ovf(ovf_path, "seed.iso")
        root = self._parse(ovf_path)
        refs = root.find(f"{{{_NS['ovf']}}}References")
        hrefs = [
            el.get(f"{{{_NS['ovf']}}}href")
            for el in refs.findall(f"{{{_NS['ovf']}}}File")
        ]
        assert hrefs.count("seed.iso") == 1

    def test_host_resource_points_to_iso(self, tmp_path):
        ovf_path = tmp_path / "test.ovf"
        ovf_path.write_text(MINIMAL_OVF, encoding="utf-8")
        inject_seed_iso_into_ovf(ovf_path, "seed.iso")
        root = self._parse(ovf_path)
        vhs = root.find(f".//{{{_NS['ovf']}}}VirtualHardwareSection")
        for item in vhs.findall(f"{{{_NS['ovf']}}}Item"):
            rtype = item.find(f"{{{_NS['rasd']}}}ResourceType")
            if rtype is not None and rtype.text == "15":
                hr = item.find(f"{{{_NS['rasd']}}}HostResource")
                assert hr is not None
                assert "seed_iso" in hr.text


# ---------------------------------------------------------------------------
# repack_ova
# ---------------------------------------------------------------------------

class TestRepackOva:
    def test_creates_tar(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "vm.ovf").write_text("content")
        (src / "disk.vmdk").write_bytes(b"\x00" * 10)

        out = tmp_path / "out.ova"
        repack_ova(src, out)
        assert tarfile.is_tarfile(str(out))

    def test_ovf_is_first_in_archive(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "vm.ovf").write_text("content")
        (src / "disk.vmdk").write_bytes(b"\x00" * 10)

        out = tmp_path / "out.ova"
        repack_ova(src, out)

        with tarfile.open(str(out)) as tar:
            members = tar.getmembers()
        assert members[0].name == "vm.ovf"


# ---------------------------------------------------------------------------
# apply_seed_iso_to_ova (integration)
# ---------------------------------------------------------------------------

class TestApplySeedIsoToOva:
    def test_creates_output_ova(self, tmp_path):
        ova = _make_ova(tmp_path / "src")
        iso = tmp_path / "seed.iso"
        iso.write_bytes(b"\x00" * 2048)  # fake ISO

        out = tmp_path / "output.ova"
        result = apply_seed_iso_to_ova(ova, iso, out)
        assert result == out
        assert out.exists()

    def test_output_contains_seed_iso(self, tmp_path):
        ova = _make_ova(tmp_path / "src")
        iso = tmp_path / "seed.iso"
        iso.write_bytes(b"\x00" * 2048)

        out = tmp_path / "output.ova"
        apply_seed_iso_to_ova(ova, iso, out)

        with tarfile.open(str(out)) as tar:
            names = tar.getnames()
        assert "seed.iso" in names
