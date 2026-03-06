"""
Tests for cloud_init_generator.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import yaml

from cloud_init_generator import (
    CloudInitConfig,
    generate_meta_data,
    generate_network_config,
    generate_user_data,
    validate_hostname,
    validate_package_name,
    validate_ssh_key,
    validate_username,
)


# ---------------------------------------------------------------------------
# generate_user_data
# ---------------------------------------------------------------------------

class TestGenerateUserData:
    def test_starts_with_cloud_config_header(self):
        cfg = CloudInitConfig()
        result = generate_user_data(cfg)
        assert result.startswith("#cloud-config\n")

    @staticmethod
    def _parse(result: str) -> dict:
        # #cloud-config is a YAML comment – safe_load handles it directly
        return yaml.safe_load(result)

    def test_hostname_in_output(self):
        cfg = CloudInitConfig()
        cfg.hostname = "my-server"
        doc = self._parse(generate_user_data(cfg))
        assert doc["hostname"] == "my-server"
        assert doc["fqdn"] == "my-server"

    def test_manage_etc_hosts(self):
        cfg = CloudInitConfig()
        cfg.hostname = "host1"
        doc = self._parse(generate_user_data(cfg))
        assert doc["manage_etc_hosts"] is True

    def test_username_in_users(self):
        cfg = CloudInitConfig()
        cfg.username = "alice"
        doc = self._parse(generate_user_data(cfg))
        users = doc["users"]
        user_names = [u["name"] if isinstance(u, dict) else u for u in users]
        assert "alice" in user_names

    def test_ssh_keys_in_output(self):
        cfg = CloudInitConfig()
        cfg.ssh_authorized_keys = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test@host"]
        doc = self._parse(generate_user_data(cfg))
        user_dicts = [u for u in doc["users"] if isinstance(u, dict)]
        assert len(user_dicts) == 1
        assert "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test@host" in user_dicts[0]["ssh_authorized_keys"]

    def test_empty_ssh_keys_excluded(self):
        cfg = CloudInitConfig()
        cfg.ssh_authorized_keys = ["", "  "]
        doc = self._parse(generate_user_data(cfg))
        user_dicts = [u for u in doc["users"] if isinstance(u, dict)]
        assert "ssh_authorized_keys" not in user_dicts[0] or user_dicts[0]["ssh_authorized_keys"] == []

    def test_packages_sorted(self):
        cfg = CloudInitConfig()
        cfg.packages = ["zsh", "curl", "git"]
        doc = self._parse(generate_user_data(cfg))
        assert doc["packages"] == sorted(["zsh", "curl", "git"])

    def test_package_update_true(self):
        cfg = CloudInitConfig()
        cfg.package_update = True
        doc = self._parse(generate_user_data(cfg))
        assert doc.get("package_update") is True

    def test_package_update_false(self):
        cfg = CloudInitConfig()
        cfg.package_update = False
        doc = self._parse(generate_user_data(cfg))
        assert "package_update" not in doc

    def test_runcmd_in_output(self):
        cfg = CloudInitConfig()
        cfg.runcmd = ["echo hello", "touch /tmp/done"]
        doc = self._parse(generate_user_data(cfg))
        assert doc["runcmd"] == ["echo hello", "touch /tmp/done"]

    def test_lock_passwd(self):
        cfg = CloudInitConfig()
        cfg.lock_passwd = True
        doc = self._parse(generate_user_data(cfg))
        user_dicts = [u for u in doc["users"] if isinstance(u, dict)]
        assert user_dicts[0]["lock_passwd"] is True

    def test_extra_keys_merged(self):
        cfg = CloudInitConfig()
        cfg.extra = {"ntp": {"servers": ["pool.ntp.org"]}}
        doc = self._parse(generate_user_data(cfg))
        assert doc["ntp"] == {"servers": ["pool.ntp.org"]}


# ---------------------------------------------------------------------------
# generate_meta_data
# ---------------------------------------------------------------------------

class TestGenerateMetaData:
    def test_instance_id_present(self):
        cfg = CloudInitConfig()
        cfg.instance_id = "test-123"
        doc = yaml.safe_load(generate_meta_data(cfg))
        assert doc["instance-id"] == "test-123"

    def test_local_hostname_present(self):
        cfg = CloudInitConfig()
        cfg.hostname = "srv01"
        doc = yaml.safe_load(generate_meta_data(cfg))
        assert doc["local-hostname"] == "srv01"


# ---------------------------------------------------------------------------
# generate_network_config
# ---------------------------------------------------------------------------

class TestGenerateNetworkConfig:
    def test_none_when_no_network_config(self):
        cfg = CloudInitConfig()
        cfg.network_config = None
        assert generate_network_config(cfg) is None

    def test_returns_yaml_string(self):
        cfg = CloudInitConfig()
        cfg.network_config = {
            "version": 2,
            "ethernets": {
                "eth0": {"dhcp4": True}
            },
        }
        result = generate_network_config(cfg)
        assert result is not None
        doc = yaml.safe_load(result)
        assert doc["version"] == 2
        assert "eth0" in doc["ethernets"]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class TestValidateHostname:
    @pytest.mark.parametrize("hostname", [
        "ubuntu-server",
        "host1",
        "my-server-01",
        "a",
    ])
    def test_valid_hostnames(self, hostname):
        assert validate_hostname(hostname) is None

    @pytest.mark.parametrize("hostname", [
        "",
        "-starts-with-dash",
        "has spaces",
        "a" * 64,  # too long
    ])
    def test_invalid_hostnames(self, hostname):
        assert validate_hostname(hostname) is not None


class TestValidateUsername:
    @pytest.mark.parametrize("username", [
        "ubuntu",
        "alice",
        "user_01",
        "_svc",
    ])
    def test_valid_usernames(self, username):
        assert validate_username(username) is None

    @pytest.mark.parametrize("username", [
        "",
        "0start-digit",
        "has space",
        "UPPER",
    ])
    def test_invalid_usernames(self, username):
        assert validate_username(username) is not None


class TestValidateSshKey:
    def test_valid_ed25519_key(self):
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest user@host"
        assert validate_ssh_key(key) is None

    def test_valid_rsa_key(self):
        key = "ssh-rsa AAAAB3NzaC1yc2EAAAA user@host"
        assert validate_ssh_key(key) is None

    def test_empty_key_is_valid(self):
        assert validate_ssh_key("") is None
        assert validate_ssh_key("   ") is None

    def test_invalid_key_type(self):
        assert validate_ssh_key("not-a-key-type AAAA") is not None

    def test_truncated_key(self):
        assert validate_ssh_key("ssh-rsa") is not None


class TestValidatePackageName:
    @pytest.mark.parametrize("name", [
        "curl",
        "python3-pip",
        "libssl1.1",
        "apt-transport-https",
    ])
    def test_valid_package_names(self, name):
        assert validate_package_name(name) is None

    @pytest.mark.parametrize("name", [
        "",
        "-starts-with-dash",
        "has space",
        "UPPER",
        "a" * 130,
    ])
    def test_invalid_package_names(self, name):
        assert validate_package_name(name) is not None
