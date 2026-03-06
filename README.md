# cloud-init-GUI

A Python/tkinter GUI for configuring a Canonical Ubuntu Server `.ova` to boot with **cloud-init**, with zero manual file editing required.

## What it does

1. Collects your VM settings through a tabbed GUI interface.
2. Generates **cloud-init** `user-data` (cloud-config YAML) and `meta-data` files.
3. Bundles them into a **NoCloud seed ISO** (volume label `cidata`) using `pycdlib`.
4. Patches the OVF descriptor inside the OVA to attach the seed ISO as a virtual CDROM drive.
5. Repacks everything into a new `.ova` ready to import into **VirtualBox**, **VMware**, or **Proxmox**.

You can also use the tool to just save the YAML files or build a standalone seed ISO without touching the OVA.

## Requirements

- Python 3.10+
- `tkinter` (usually shipped with Python; on Ubuntu/Debian: `sudo apt install python3-tk`)
- See `requirements.txt` for Python package dependencies

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python app.py
```

### Tabs

| Tab | What you configure |
|-----|--------------------|
| **OVA File** | Browse for the source Ubuntu Server `.ova` |
| **Instance** | Hostname and cloud-init instance ID |
| **User Account** | Default username, password, SSH authorised keys |
| **Network** | DHCP (default) or static IP / gateway / DNS |
| **Packages & Commands** | `apt` packages to install, `runcmd` first-boot commands |
| **Preview** | Live preview of the generated `user-data`, `meta-data`, `network-config` |

### Bottom buttons

| Button | Action |
|--------|--------|
| **Preview YAML** | Generate and display the cloud-init YAML in the Preview tab |
| **Save YAML files…** | Write `user-data`, `meta-data` (and optionally `network-config`) to a folder |
| **Build seed ISO…** | Create a standalone `seed.iso` (NoCloud `cidata` ISO) |
| **Apply to OVA…** | Inject the seed ISO into a copy of the selected OVA and save the result |

## Project structure

```
app.py                  – Main GUI entry point (tkinter)
cloud_init_generator.py – Generates user-data / meta-data YAML
iso_builder.py          – Builds the NoCloud seed ISO (pycdlib)
ova_handler.py          – Extracts, patches, and repacks OVA files
requirements.txt        – Python dependencies
tests/
  test_cloud_init_generator.py
  test_iso_builder.py
  test_ova_handler.py
```

## Running the tests

```bash
pip install pytest
pytest tests/ -v
```

## How cloud-init NoCloud works

When an Ubuntu Server VM boots it looks for cloud-init data on a drive with the volume label `cidata`.  This tool creates an ISO with that label containing:

- `/user-data` – the main cloud-config YAML (hostname, users, packages, commands)
- `/meta-data` – instance ID and local hostname
- `/network-config` *(optional)* – Netplan v2 network configuration

The ISO is attached as a virtual CDROM in the OVF descriptor so the hypervisor presents it automatically at first boot.
