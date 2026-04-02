"""
guestinfo_handler.py
Injects cloud-init configuration into an OVA via VMware OVF guestinfo
properties, for ESXi 7 and VMware-based deployments.

Unlike the NoCloud seed-ISO approach (which attaches a CDROM), this method
embeds base64-encoded cloud-init payloads directly in the OVF descriptor as
ProductSection properties.  When the VM boots, open-vm-tools reads those
properties and hands them to cloud-init — no separate ISO file required.

After patching the OVF the SHA-256 manifest (.mf) is regenerated.
ESXi validates the manifest on import and will reject the OVA if any
checksums are stale.

Compatible targets:
  - ESXi 7 (primary)
  - VMware Workstation / Fusion
  - Proxmox with open-vm-tools (emerging support)
"""

from __future__ import annotations

import base64
import hashlib
import os
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Re-use the namespace registry from ova_handler so both modules share
# a single source of truth and namespaces are registered only once.
from ova_handler import _NS, extract_ova, find_ovf


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def b64_encode(text: str) -> str:
    """Base64-encode a UTF-8 string and return the ASCII result."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# OVF guestinfo injection
# ---------------------------------------------------------------------------

def inject_guestinfo_into_ovf(
    ovf_path: str | os.PathLike,
    userdata_b64: str,
    metadata_b64: str,
) -> None:
    """
    Patch the OVF XML at *ovf_path* in-place to add (or replace) a
    ProductSection carrying base64-encoded guestinfo properties.

    The four properties written are:
      guestinfo.metadata          – base64 cloud-init meta-data
      guestinfo.metadata.encoding – "base64"
      guestinfo.userdata          – base64 cloud-init user-data
      guestinfo.userdata.encoding – "base64"

    The operation is idempotent: any existing cloud-init ProductSection
    (detected by the presence of "guestinfo" keys) is removed first.
    """
    ovf_path = Path(ovf_path)
    tree = ET.parse(str(ovf_path))
    root = tree.getroot()

    ovf_ns = _NS["ovf"]

    def qn(ns_uri: str, tag: str) -> str:
        return f"{{{ns_uri}}}{tag}"

    # Locate the VirtualSystem element (parent of ProductSection).
    # Some OVFs place ProductSection directly under the Envelope root.
    vs = root.find(f".//{qn(ovf_ns, 'VirtualSystem')}")
    if vs is None:
        vs = root

    # Remove any existing cloud-init ProductSection (idempotent re-run)
    for ps in list(vs.findall(qn(ovf_ns, "ProductSection"))):
        keys = [
            p.get(qn(ovf_ns, "key"), "")
            for p in ps.findall(qn(ovf_ns, "Property"))
        ]
        if any("guestinfo" in k for k in keys):
            vs.remove(ps)

    # Build new ProductSection with the four guestinfo properties
    ps = ET.SubElement(vs, qn(ovf_ns, "ProductSection"))
    info_el = ET.SubElement(ps, qn(ovf_ns, "Info"))
    info_el.text = "Cloud-init configuration injected by cloud-init-gui"

    def _add_prop(key: str, value: str, label_text: str) -> None:
        prop = ET.SubElement(ps, qn(ovf_ns, "Property"))
        prop.set(qn(ovf_ns, "key"),   key)
        prop.set(qn(ovf_ns, "type"),  "string")
        prop.set(qn(ovf_ns, "value"), value)
        lbl = ET.SubElement(prop, qn(ovf_ns, "Label"))
        lbl.text = label_text

    _add_prop("guestinfo.metadata",          metadata_b64, "Cloud-init meta-data (base64)")
    _add_prop("guestinfo.metadata.encoding", "base64",     "Meta-data encoding")
    _add_prop("guestinfo.userdata",          userdata_b64, "Cloud-init user-data (base64)")
    _add_prop("guestinfo.userdata.encoding", "base64",     "User-data encoding")

    tree.write(
        str(ovf_path),
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=False,
    )


# ---------------------------------------------------------------------------
# Manifest regeneration
# ---------------------------------------------------------------------------

def regenerate_manifest(work_dir: str | os.PathLike) -> Path:
    """
    Regenerate the SHA-256 .mf manifest file inside *work_dir*.

    ESXi validates this manifest at import time and will reject the OVA
    if any checksum is stale.  Must be called after any OVF modification.

    If an existing .mf file is found it is updated in place; otherwise a
    new one is created named after the OVF file (e.g. ``ubuntu.mf``).

    Returns the path of the written manifest file.
    """
    work_dir = Path(work_dir)
    mf_files = list(work_dir.glob("*.mf"))

    if mf_files:
        mf_path = mf_files[0]
    else:
        ovf_files = list(work_dir.glob("*.ovf"))
        stem = ovf_files[0].stem if ovf_files else "disk"
        mf_path = work_dir / f"{stem}.mf"

    non_mf = sorted(f for f in work_dir.iterdir() if f.suffix != ".mf" and f.is_file())
    lines = [f"SHA256({f.name})= {sha256_file(f)}" for f in non_mf]
    mf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mf_path


# ---------------------------------------------------------------------------
# OVA repacking (ESXi-safe ordering)
# ---------------------------------------------------------------------------

def repack_ova_esxi(
    source_dir: str | os.PathLike,
    output_path: str | os.PathLike,
) -> Path:
    """
    Repack *source_dir* into an OVA at *output_path* using the file
    ordering required by ESXi:

      1. OVF descriptor  (.ovf)  — must be first
      2. Disk images     (.vmdk) — in name order
      3. Manifest        (.mf)   — must be last

    Returns the path of the created OVA.
    """
    source_dir  = Path(source_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _sort_key(p: Path) -> tuple:
        if p.suffix == ".ovf":
            return (0, p.name)
        if p.suffix == ".mf":
            return (2, p.name)
        return (1, p.name)

    files = sorted(
        (f for f in source_dir.iterdir() if f.is_file()),
        key=_sort_key,
    )

    with tarfile.open(str(output_path), "w") as tar:
        for f in files:
            tar.add(str(f), arcname=f.name)

    return output_path


# ---------------------------------------------------------------------------
# High-level orchestrator
# ---------------------------------------------------------------------------

def apply_guestinfo_to_ova(
    ova_path: str | os.PathLike,
    user_data: str,
    meta_data: str,
    output_ova_path: str | os.PathLike,
) -> Path:
    """
    Inject guestinfo properties into a copy of *ova_path* and write the
    result to *output_ova_path*.

    Pipeline:
      1. Extract OVA to a temporary directory.
      2. Base64-encode user-data and meta-data.
      3. Inject guestinfo properties into the OVF descriptor.
      4. Regenerate the SHA-256 manifest (.mf).
      5. Repack into a new OVA with ESXi-safe file ordering.

    Returns the path of the created OVA.
    """
    ova_path        = Path(ova_path)
    output_ova_path = Path(output_ova_path)

    with tempfile.TemporaryDirectory(prefix="cloud-init-gui-guestinfo-") as tmp:
        tmp_dir = Path(tmp)

        extract_ova(ova_path, tmp_dir)
        ovf_path = find_ovf(tmp_dir)

        inject_guestinfo_into_ovf(
            ovf_path,
            b64_encode(user_data),
            b64_encode(meta_data),
        )
        regenerate_manifest(tmp_dir)
        repack_ova_esxi(tmp_dir, output_ova_path)

    return output_ova_path
