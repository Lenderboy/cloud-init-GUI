"""
iso_builder.py
Build a cloud-init NoCloud seed ISO using pycdlib.

The ISO must have the volume label ``cidata`` and contain two files:
  - /meta-data
  - /user-data
Optionally also:
  - /network-config
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pycdlib


def build_seed_iso(
    user_data: str,
    meta_data: str,
    output_path: str | os.PathLike,
    network_config: str | None = None,
) -> Path:
    """
    Create a cloud-init NoCloud seed ISO at *output_path*.

    Parameters
    ----------
    user_data:       Content of the ``user-data`` file.
    meta_data:       Content of the ``meta-data`` file.
    output_path:     Where to write the resulting ``.iso`` file.
    network_config:  Optional content of the ``network-config`` file.

    Returns
    -------
    Path to the created ISO file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    iso = pycdlib.PyCdlib()
    iso.new(
        interchange_level=4,   # allows long file names
        joliet=3,              # Joliet extension for Windows compat
        rock_ridge="1.09",     # Rock Ridge for POSIX attrs
        vol_ident="CIDATA",    # cloud-init looks for "cidata" (case-insensitive)
    )

    def _add_file(content: str, iso_path: str, rr_name: str, joliet_path: str) -> None:
        data = content.encode("utf-8")
        iso.add_fp(
            io.BytesIO(data),
            len(data),
            iso_path=iso_path,
            rr_name=rr_name,
            joliet_path=joliet_path,
        )

    _add_file(meta_data,  "/METADATA.;1",       "meta-data",       "/meta-data")
    _add_file(user_data,  "/USERDATA.;1",        "user-data",       "/user-data")

    if network_config is not None:
        _add_file(
            network_config,
            "/NETWORKCONFIG.;1",
            "network-config",
            "/network-config",
        )

    iso.write(str(output_path))
    iso.close()
    return output_path
