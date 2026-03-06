"""
ova_handler.py
Utilities for reading and modifying OVA files.

An OVA is a tar archive containing at minimum:
  - One ``.ovf`` descriptor (XML)
  - One or more disk images (``.vmdk``, ``.qcow2``, etc.)

To inject a cloud-init seed ISO this module:
  1. Extracts the OVA to a temporary directory.
  2. Copies the seed ISO into that directory.
  3. Patches the ``.ovf`` XML to add a new CDROM device referencing the ISO.
  4. Repacks everything into a new ``.ova`` file.

The resulting OVA can be imported into VirtualBox, VMware, Proxmox, etc.
The CDROM with ``cidata`` label is picked up automatically by cloud-init.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


# OVF XML namespaces used in Ubuntu/Canonical OVAs
_NS: dict[str, str] = {
    "ovf":   "http://schemas.dmtf.org/ovf/envelope/1",
    "rasd":  "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData",
    "vssd":  "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData",
    "vmw":   "http://www.vmware.com/schema/ovf",
    "xsi":   "http://www.w3.org/2001/XMLSchema-instance",
}

for _prefix, _uri in _NS.items():
    ET.register_namespace(_prefix, _uri)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def extract_ova(ova_path: str | os.PathLike, dest_dir: str | os.PathLike) -> Path:
    """
    Extract *ova_path* into *dest_dir*.

    Returns the path to the extracted directory.
    """
    ova_path = Path(ova_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not tarfile.is_tarfile(str(ova_path)):
        raise ValueError(f"{ova_path} does not appear to be a valid OVA (tar) file.")

    with tarfile.open(str(ova_path), "r") as tar:
        # Safety: reject absolute paths and path traversal
        for member in tar.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(
                    f"Refusing to extract unsafe path: {member.name!r}"
                )
        # filter='data' (Python 3.11.4+) sanitises extracted file metadata;
        # fall back gracefully on older interpreters.
        import sys  # noqa: PLC0415
        _extract_kwargs: dict = {"filter": "data"} if sys.version_info >= (3, 11, 4) else {}
        tar.extractall(dest_dir, **_extract_kwargs)  # noqa: S202 – validated above

    return dest_dir


def find_ovf(directory: str | os.PathLike) -> Path:
    """Return the path to the single ``.ovf`` file inside *directory*."""
    directory = Path(directory)
    ovf_files = list(directory.glob("*.ovf"))
    if not ovf_files:
        raise FileNotFoundError(f"No .ovf file found in {directory}")
    if len(ovf_files) > 1:
        raise ValueError(f"Multiple .ovf files found in {directory}: {ovf_files}")
    return ovf_files[0]


def inject_seed_iso_into_ovf(ovf_path: str | os.PathLike, iso_name: str) -> None:
    """
    Patch the OVF XML at *ovf_path* in-place to add a CDROM device
    referencing the file *iso_name* (relative filename, not full path).

    If a CDROM device already exists it is updated; otherwise a new one is added.
    """
    ovf_path = Path(ovf_path)
    tree = ET.parse(str(ovf_path))
    root = tree.getroot()

    # 1. Add a References/File entry for the ISO
    _ensure_file_reference(root, iso_name)

    # 2. Add/update DiskSection & VirtualHardwareSection entries
    _ensure_cdrom_hardware_item(root, iso_name)

    tree.write(str(ovf_path), xml_declaration=True, encoding="utf-8")


def repack_ova(
    source_dir: str | os.PathLike,
    output_path: str | os.PathLike,
) -> Path:
    """
    Re-create an OVA (tar) from all files in *source_dir* and write it to *output_path*.

    The ``.ovf`` file is placed first in the archive (as required by the spec),
    followed by all other files.
    """
    source_dir = Path(source_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(source_dir.iterdir())
    ovf_files = [f for f in files if f.suffix == ".ovf"]
    other_files = [f for f in files if f.suffix != ".ovf"]

    with tarfile.open(str(output_path), "w") as tar:
        for f in ovf_files + other_files:
            tar.add(str(f), arcname=f.name)

    return output_path


def apply_seed_iso_to_ova(
    ova_path: str | os.PathLike,
    seed_iso_path: str | os.PathLike,
    output_ova_path: str | os.PathLike,
) -> Path:
    """
    High-level helper: inject *seed_iso_path* into a copy of *ova_path* and
    write the result to *output_ova_path*.

    Returns the path of the created OVA.
    """
    ova_path = Path(ova_path)
    seed_iso_path = Path(seed_iso_path)
    output_ova_path = Path(output_ova_path)

    with tempfile.TemporaryDirectory(prefix="cloud-init-gui-") as tmp:
        tmp_dir = Path(tmp)

        # Extract original OVA
        extract_ova(ova_path, tmp_dir)

        # Copy seed ISO into the work directory
        iso_dest = tmp_dir / seed_iso_path.name
        shutil.copy2(str(seed_iso_path), str(iso_dest))

        # Patch the OVF
        ovf_path = find_ovf(tmp_dir)
        inject_seed_iso_into_ovf(ovf_path, seed_iso_path.name)

        # Repack
        repack_ova(tmp_dir, output_ova_path)

    return output_ova_path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _ovf_tag(local: str) -> str:
    return f"{{{_NS['ovf']}}}{local}"


def _rasd_tag(local: str) -> str:
    return f"{{{_NS['rasd']}}}{local}"


def _ensure_file_reference(root: ET.Element, iso_name: str) -> None:
    """Add a <File> entry under <References> if not already present."""
    refs = root.find(_ovf_tag("References"))
    if refs is None:
        refs = ET.SubElement(root, _ovf_tag("References"))

    # Check if the file is already referenced
    for file_el in refs.findall(_ovf_tag("File")):
        if file_el.get(f"{{{_NS['ovf']}}}href") == iso_name:
            return  # already present

    file_id = iso_name.replace(".", "_").replace("-", "_")
    file_el = ET.SubElement(refs, _ovf_tag("File"))
    file_el.set(f"{{{_NS['ovf']}}}href", iso_name)
    file_el.set(f"{{{_NS['ovf']}}}id", file_id)


def _ensure_cdrom_hardware_item(root: ET.Element, iso_name: str) -> None:
    """
    Add CDROM ResourceAllocationSettingData items to VirtualHardwareSection.
    Inserts a controller + drive if no CDROM is present, or updates
    the existing HostResource if one is.
    """
    # Find the VirtualHardwareSection
    vhs = root.find(f".//{_ovf_tag('VirtualHardwareSection')}")
    if vhs is None:
        # Cannot patch; just return gracefully
        return

    file_id = iso_name.replace(".", "_").replace("-", "_")
    iso_ref = f"ovf:/file/{file_id}"

    # Look for an existing CDROM item (ResourceType 15)
    for item in vhs.findall(_ovf_tag("Item")):
        rtype = item.find(_rasd_tag("ResourceType"))
        if rtype is not None and rtype.text == "15":
            # Update or add HostResource
            hr = item.find(_rasd_tag("HostResource"))
            if hr is None:
                hr = ET.SubElement(item, _rasd_tag("HostResource"))
            hr.text = iso_ref
            return

    # No existing CDROM – determine next free InstanceID
    instance_ids = []
    for item in vhs.findall(_ovf_tag("Item")):
        iid = item.find(_rasd_tag("InstanceID"))
        if iid is not None:
            try:
                instance_ids.append(int(iid.text))
            except (ValueError, TypeError):
                pass
    next_id = str(max(instance_ids, default=10) + 1)

    # Find or create an IDE controller (ResourceType 5)
    ctrl_id: str | None = None
    for item in vhs.findall(_ovf_tag("Item")):
        rtype = item.find(_rasd_tag("ResourceType"))
        if rtype is not None and rtype.text == "5":
            iid = item.find(_rasd_tag("InstanceID"))
            if iid is not None:
                ctrl_id = iid.text
            break

    if ctrl_id is None:
        # Create a new IDE controller
        ctrl_el = ET.SubElement(vhs, _ovf_tag("Item"))
        _rasd_sub(ctrl_el, "Description", "IDE Controller")
        _rasd_sub(ctrl_el, "ElementName", "IDE Controller 1")
        _rasd_sub(ctrl_el, "InstanceID", next_id)
        _rasd_sub(ctrl_el, "ResourceType", "5")
        ctrl_id = next_id
        next_id = str(int(next_id) + 1)

    # Create CDROM drive
    cdrom_el = ET.SubElement(vhs, _ovf_tag("Item"))
    _rasd_sub(cdrom_el, "AddressOnParent", "0")
    _rasd_sub(cdrom_el, "Description", "CD/DVD Drive")
    _rasd_sub(cdrom_el, "ElementName", "CD/DVD Drive 1")
    _rasd_sub(cdrom_el, "HostResource", iso_ref)
    _rasd_sub(cdrom_el, "InstanceID", next_id)
    _rasd_sub(cdrom_el, "Parent", ctrl_id)
    _rasd_sub(cdrom_el, "ResourceType", "15")


def _rasd_sub(parent: ET.Element, local: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, _rasd_tag(local))
    el.text = text
    return el
