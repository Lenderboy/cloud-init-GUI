"""
cloud_init_generator.py
Generate cloud-init user-data (cloud-config YAML) and meta-data files
for the NoCloud datasource.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Data classes / helpers
# ---------------------------------------------------------------------------

class CloudInitConfig:
    """Holds all settings required to generate cloud-init files."""

    def __init__(self) -> None:
        # Instance metadata
        self.instance_id: str = "cloud-init-gui-instance"
        self.hostname: str = "ubuntu-server"

        # Default user
        self.username: str = "ubuntu"
        self.password: str = ""                # plain-text; hashed before output
        self.ssh_authorized_keys: list[str] = []
        self.lock_passwd: bool = False         # True = disable password login
        self.sudo: str = "ALL=(ALL) NOPASSWD:ALL"

        # Network (None means use DHCP for all interfaces)
        self.network_config: dict[str, Any] | None = None

        # Packages to install
        self.packages: list[str] = []
        self.package_update: bool = True
        self.package_upgrade: bool = False

        # Commands to run on first boot
        self.runcmd: list[str] = []

        # Optional: additional cloud-config keys (merged verbatim)
        self.extra: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# YAML generation
# ---------------------------------------------------------------------------

def _hash_password(plain: str) -> str:
    """Return a SHA-512 crypt hash suitable for cloud-init chpasswd."""
    import crypt  # noqa: PLC0415 – stdlib, only available on POSIX
    return crypt.crypt(plain, crypt.mksalt(crypt.METHOD_SHA512))


def generate_user_data(cfg: CloudInitConfig) -> str:
    """
    Build and return the ``user-data`` file content as a string.
    The file starts with ``#cloud-config`` and contains YAML.
    """
    doc: dict[str, Any] = {}

    # ---- hostname ----
    if cfg.hostname:
        doc["hostname"] = cfg.hostname
        doc["fqdn"] = cfg.hostname
        doc["manage_etc_hosts"] = True

    # ---- users ----
    if cfg.username:
        user: dict[str, Any] = {
            "name": cfg.username,
            "groups": ["sudo"],
            "shell": "/bin/bash",
            "lock_passwd": cfg.lock_passwd,
        }
        if cfg.sudo:
            user["sudo"] = cfg.sudo
        if cfg.ssh_authorized_keys:
            user["ssh_authorized_keys"] = [
                k.strip() for k in cfg.ssh_authorized_keys if k.strip()
            ]
        if cfg.password and not cfg.lock_passwd:
            try:
                user["passwd"] = _hash_password(cfg.password)
            except (ImportError, AttributeError):
                # crypt not available (Windows); store plain as a reminder
                user["passwd"] = cfg.password
        doc["users"] = ["default", user]

        if cfg.password and not cfg.lock_passwd:
            doc["chpasswd"] = {"expire": False}

    # ---- package management ----
    if cfg.package_update:
        doc["package_update"] = True
    if cfg.package_upgrade:
        doc["package_upgrade"] = True
    if cfg.packages:
        doc["packages"] = sorted(cfg.packages)

    # ---- runcmd ----
    if cfg.runcmd:
        doc["runcmd"] = cfg.runcmd

    # ---- extra keys ----
    doc.update(cfg.extra)

    yaml_text = yaml.dump(doc, default_flow_style=False, allow_unicode=True)
    return "#cloud-config\n" + yaml_text


def generate_meta_data(cfg: CloudInitConfig) -> str:
    """
    Build and return the ``meta-data`` file content (YAML).
    """
    doc: dict[str, Any] = {
        "instance-id": cfg.instance_id,
        "local-hostname": cfg.hostname,
    }
    return yaml.dump(doc, default_flow_style=False, allow_unicode=True)


def generate_network_config(cfg: CloudInitConfig) -> str | None:
    """
    Return the ``network-config`` file content, or ``None`` if not configured.
    """
    if cfg.network_config is None:
        return None
    return yaml.dump(cfg.network_config, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_HOSTNAME_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z]{2,})?$"
)
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def validate_hostname(hostname: str) -> str | None:
    """Return an error message, or None if valid."""
    if not hostname:
        return "Hostname must not be empty."
    if not _HOSTNAME_RE.match(hostname):
        return "Hostname may contain letters, digits and hyphens only."
    return None


def validate_username(username: str) -> str | None:
    """Return an error message, or None if valid."""
    if not username:
        return "Username must not be empty."
    if not _USERNAME_RE.match(username):
        return "Username must start with a letter/underscore and contain only a-z, 0-9, _ or -."
    return None


def validate_ssh_key(key: str) -> str | None:
    """Return an error message, or None if the key looks valid."""
    key = key.strip()
    if not key:
        return None  # empty is fine (will be skipped)
    valid_types = (
        "ssh-rsa", "ssh-dss", "ssh-ed25519",
        "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    )
    parts = key.split()
    if not parts or parts[0] not in valid_types:
        return f"SSH key must start with one of: {', '.join(valid_types)}"
    if len(parts) < 2:
        return "SSH key appears to be truncated."
    return None


def validate_package_name(name: str) -> str | None:
    """Return an error message, or None if valid."""
    if not re.match(r"^[a-z0-9][a-z0-9+\-.]{0,127}$", name):
        return f"Invalid package name: {name!r}"
    return None
