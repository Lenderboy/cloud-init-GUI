"""
ova_downloader.py
Streaming download of Ubuntu Server cloud OVAs from Canonical's official
image server, with progress reporting.

No third-party libraries required — uses only Python stdlib (urllib).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Ubuntu cloud image registry (Canonical official URLs)
# ---------------------------------------------------------------------------

UBUNTU_VERSIONS: dict[str, dict[str, str]] = {
    "Ubuntu 24.04 LTS (Noble Numbat)": {
        "url":      "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.ova",
        "codename": "noble",
    },
    "Ubuntu 22.04 LTS (Jammy Jellyfish)": {
        "url":      "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.ova",
        "codename": "jammy",
    },
    "Ubuntu 20.04 LTS (Focal Fossa)": {
        "url":      "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.ova",
        "codename": "focal",
    },
}

_USER_AGENT = "cloud-init-gui/2.0 (github.com/gingersnap)"
_CHUNK_SIZE = 1 << 17  # 128 KB


# ---------------------------------------------------------------------------
# Download function
# ---------------------------------------------------------------------------

def download_ova(
    url: str,
    dest_path: str | os.PathLike,
    progress_callback: Callable[[int, str], None] | None = None,
) -> Path:
    """
    Download *url* to *dest_path*, streaming in chunks and reporting
    progress via *progress_callback(pct, message)*.

    ``pct`` is 0–100 when Content-Length is known; -1 when unknown.

    Returns the path of the completed download.
    Raises ``RuntimeError`` on network failure (partial file is removed).
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    req = Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urlopen(req, timeout=120) as resp:
            total     = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0

            with open(dest_path, "wb") as fh:
                while True:
                    data = resp.read(_CHUNK_SIZE)
                    if not data:
                        break
                    fh.write(data)
                    downloaded += len(data)

                    if progress_callback:
                        mb       = downloaded // (1024 * 1024)
                        total_mb = total      // (1024 * 1024)
                        if total:
                            pct = min(int(downloaded / total * 100), 99)
                            progress_callback(pct, f"Downloading… {mb} / {total_mb} MB")
                        else:
                            progress_callback(-1, f"Downloading… {mb} MB")

    except URLError as exc:
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download failed: {exc.reason if hasattr(exc, 'reason') else exc}"
        ) from exc

    if progress_callback:
        size_mb = dest_path.stat().st_size // (1024 * 1024)
        progress_callback(100, f"Download complete — {size_mb} MB")

    return dest_path
