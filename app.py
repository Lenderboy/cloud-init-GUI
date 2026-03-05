"""
app.py
GUI front-end for preparing a Canonical Ubuntu Server .ova to run with cloud-init.

Usage:
    python app.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from tkinter import (
    BooleanVar,
    END,
    StringVar,
    Text,
    Tk,
    filedialog,
    messagebox,
    scrolledtext,
    ttk,
)
import tkinter as tk

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
from iso_builder import build_seed_iso
from ova_handler import apply_seed_iso_to_ova


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label(parent: tk.Widget, text: str, row: int, col: int = 0, **kw) -> ttk.Label:
    lbl = ttk.Label(parent, text=text, **kw)
    lbl.grid(row=row, column=col, sticky="w", padx=8, pady=4)
    return lbl


def _entry(parent: tk.Widget, var: StringVar, row: int, col: int = 1,
           width: int = 40, **kw) -> ttk.Entry:
    ent = ttk.Entry(parent, textvariable=var, width=width, **kw)
    ent.grid(row=row, column=col, sticky="ew", padx=8, pady=4)
    return ent


def _check(parent: tk.Widget, text: str, var: BooleanVar,
           row: int, col: int = 1) -> ttk.Checkbutton:
    cb = ttk.Checkbutton(parent, text=text, variable=var)
    cb.grid(row=row, column=col, sticky="w", padx=8, pady=4)
    return cb


# ---------------------------------------------------------------------------
# Main Application Window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("cloud-init GUI  –  Ubuntu Server OVA Configurator")
        self.resizable(True, True)
        self.minsize(700, 560)

        # ---------- Style ----------
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # ---------- Notebook ----------
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # Build tabs
        self._tab_ova = OvaTab(self._notebook)
        self._tab_instance = InstanceTab(self._notebook)
        self._tab_user = UserTab(self._notebook)
        self._tab_network = NetworkTab(self._notebook)
        self._tab_packages = PackagesTab(self._notebook)
        self._tab_preview = PreviewTab(self._notebook)

        self._notebook.add(self._tab_ova,      text=" OVA File ")
        self._notebook.add(self._tab_instance, text=" Instance ")
        self._notebook.add(self._tab_user,     text=" User Account ")
        self._notebook.add(self._tab_network,  text=" Network ")
        self._notebook.add(self._tab_packages, text=" Packages & Commands ")
        self._notebook.add(self._tab_preview,  text=" Preview ")

        # ---------- Bottom button bar ----------
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Button(btn_frame, text="Preview YAML",
                   command=self._on_preview).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Save YAML files…",
                   command=self._on_save_yaml).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Build seed ISO…",
                   command=self._on_build_iso).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Apply to OVA…",
                   command=self._on_apply_to_ova).pack(side="left", padx=4)

        self._status = StringVar(value="Ready.")
        ttk.Label(btn_frame, textvariable=self._status,
                  foreground="gray").pack(side="right", padx=8)

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_config(self) -> CloudInitConfig | None:
        """Collect settings from all tabs and return a CloudInitConfig.
        Returns None and shows an error dialog if validation fails.
        """
        cfg = CloudInitConfig()

        # Instance
        cfg.hostname = self._tab_instance.hostname.get().strip()
        cfg.instance_id = self._tab_instance.instance_id.get().strip() or "cloud-init-gui"

        err = validate_hostname(cfg.hostname)
        if err:
            messagebox.showerror("Validation Error", f"Hostname: {err}")
            return None

        # User
        cfg.username = self._tab_user.username.get().strip()
        err = validate_username(cfg.username)
        if err:
            messagebox.showerror("Validation Error", f"Username: {err}")
            return None

        cfg.password = self._tab_user.password.get()
        cfg.lock_passwd = self._tab_user.lock_passwd.get()

        raw_keys = self._tab_user.ssh_keys.get("1.0", END).strip()
        cfg.ssh_authorized_keys = [k for k in raw_keys.splitlines() if k.strip()]
        for key in cfg.ssh_authorized_keys:
            err = validate_ssh_key(key)
            if err:
                messagebox.showerror("Validation Error", f"SSH key: {err}")
                return None

        # Network
        cfg.network_config = self._tab_network.get_network_config()

        # Packages
        cfg.package_update = self._tab_packages.pkg_update.get()
        cfg.package_upgrade = self._tab_packages.pkg_upgrade.get()
        raw_pkgs = self._tab_packages.packages.get("1.0", END).strip()
        cfg.packages = [p.strip() for p in raw_pkgs.splitlines() if p.strip()]
        for pkg in cfg.packages:
            err = validate_package_name(pkg)
            if err:
                messagebox.showerror("Validation Error", err)
                return None

        raw_cmd = self._tab_packages.runcmd.get("1.0", END).strip()
        cfg.runcmd = [c for c in raw_cmd.splitlines() if c.strip()]

        return cfg

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_preview(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return
        ud = generate_user_data(cfg)
        md = generate_meta_data(cfg)
        nc = generate_network_config(cfg)

        self._tab_preview.set_content(ud, md, nc)
        # Switch to the Preview tab by widget reference (no magic index)
        self._notebook.select(self._tab_preview)

    def _on_save_yaml(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return

        directory = filedialog.askdirectory(title="Choose output folder")
        if not directory:
            return

        out = Path(directory)
        ud = generate_user_data(cfg)
        md = generate_meta_data(cfg)
        nc = generate_network_config(cfg)

        (out / "user-data").write_text(ud, encoding="utf-8")
        (out / "meta-data").write_text(md, encoding="utf-8")
        if nc is not None:
            (out / "network-config").write_text(nc, encoding="utf-8")

        self._status.set(f"Saved to {out}")
        messagebox.showinfo("Saved", f"cloud-init files saved to:\n{out}")

    def _on_build_iso(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return

        out_path = filedialog.asksaveasfilename(
            title="Save seed ISO as…",
            defaultextension=".iso",
            filetypes=[("ISO image", "*.iso"), ("All files", "*.*")],
            initialfile="seed.iso",
        )
        if not out_path:
            return

        self._status.set("Building ISO…")
        self.update_idletasks()

        try:
            ud = generate_user_data(cfg)
            md = generate_meta_data(cfg)
            nc = generate_network_config(cfg)
            build_seed_iso(ud, md, out_path, network_config=nc)
            self._status.set(f"ISO saved: {out_path}")
            messagebox.showinfo("Success", f"Seed ISO written to:\n{out_path}")
        except Exception as exc:  # noqa: BLE001
            self._status.set("Error building ISO.")
            messagebox.showerror("Error", f"Failed to build ISO:\n{exc}")

    def _on_apply_to_ova(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return

        ova_in = self._tab_ova.ova_path.get().strip()
        if not ova_in or not Path(ova_in).is_file():
            messagebox.showerror(
                "No OVA selected",
                "Please select a source OVA file on the 'OVA File' tab.",
            )
            return

        out_path = filedialog.asksaveasfilename(
            title="Save configured OVA as…",
            defaultextension=".ova",
            filetypes=[("OVA file", "*.ova"), ("All files", "*.*")],
            initialfile=Path(ova_in).stem + "-cloud-init.ova",
        )
        if not out_path:
            return

        self._status.set("Working…")
        self.update_idletasks()

        def _worker() -> None:
            try:
                ud = generate_user_data(cfg)
                md = generate_meta_data(cfg)
                nc = generate_network_config(cfg)

                with tempfile.TemporaryDirectory(prefix="cloud-init-gui-") as tmp:
                    iso_path = Path(tmp) / "seed.iso"
                    build_seed_iso(ud, md, str(iso_path), network_config=nc)
                    apply_seed_iso_to_ova(ova_in, str(iso_path), out_path)

                self.after(0, lambda: self._status.set(f"OVA saved: {out_path}"))
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Success",
                        f"Configured OVA written to:\n{out_path}\n\n"
                        "Import it into VirtualBox / VMware / Proxmox to boot "
                        "with cloud-init.",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                self.after(0, lambda: self._status.set("Error."))
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Error", f"Failed to apply to OVA:\n{exc}\n\n{tb}"
                    ),
                )

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Tab widgets
# ---------------------------------------------------------------------------

class OvaTab(ttk.Frame):
    """Tab for selecting the input OVA file."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(1, weight=1)

        self.ova_path = StringVar()

        _label(self, "Source OVA file:", 0)
        _entry(self, self.ova_path, 0, width=50)
        ttk.Button(self, text="Browse…",
                   command=self._browse).grid(row=0, column=2, padx=4, pady=4)

        info = (
            "Select a Canonical Ubuntu Server .ova file.\n\n"
            "This tool will:\n"
            "  1. Generate cloud-init user-data and meta-data files.\n"
            "  2. Bundle them into a NoCloud seed ISO (cidata label).\n"
            "  3. Patch the OVF descriptor to attach the ISO as a CDROM drive.\n"
            "  4. Repack everything into a new .ova ready for import.\n\n"
            "You can also save the YAML files or seed ISO separately using\n"
            "the buttons at the bottom of the window.\n\n"
            "Note: You do not have to select an OVA to save YAML or an ISO."
        )
        ttk.Label(self, text=info, justify="left",
                  wraplength=580).grid(
            row=1, column=0, columnspan=3, padx=12, pady=16, sticky="w"
        )

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Ubuntu Server OVA",
            filetypes=[("OVA files", "*.ova"), ("All files", "*.*")],
        )
        if path:
            self.ova_path.set(path)


class InstanceTab(ttk.Frame):
    """Tab for VM / instance metadata."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(1, weight=1)

        self.hostname = StringVar(value="ubuntu-server")
        self.instance_id = StringVar(value="cloud-init-gui-instance")

        _label(self, "Hostname:", 0)
        _entry(self, self.hostname, 0)
        ttk.Label(self, text="e.g. my-server", foreground="gray").grid(
            row=0, column=2, sticky="w", padx=4)

        _label(self, "Instance ID:", 1)
        _entry(self, self.instance_id, 1)
        ttk.Label(self, text="Unique ID for this cloud-init run", foreground="gray").grid(
            row=1, column=2, sticky="w", padx=4)


class UserTab(ttk.Frame):
    """Tab for default user configuration."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(1, weight=1)

        self.username = StringVar(value="ubuntu")
        self.password = StringVar()
        self.lock_passwd = BooleanVar(value=False)

        _label(self, "Username:", 0)
        _entry(self, self.username, 0)

        _label(self, "Password:", 1)
        pwd_entry = ttk.Entry(self, textvariable=self.password, width=40, show="*")
        pwd_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)

        _check(self, "Lock password (SSH key login only)", self.lock_passwd, 2)

        _label(self, "SSH Authorized Keys:", 3)
        ttk.Label(self, text="(one key per line)", foreground="gray").grid(
            row=3, column=2, sticky="w", padx=4)

        self.ssh_keys = scrolledtext.ScrolledText(self, width=50, height=8)
        self.ssh_keys.grid(row=4, column=0, columnspan=3,
                           padx=8, pady=4, sticky="nsew")
        self.rowconfigure(4, weight=1)


class NetworkTab(ttk.Frame):
    """Tab for network configuration."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(1, weight=1)

        self._mode = StringVar(value="dhcp")

        ttk.Label(self, text="Network configuration:").grid(
            row=0, column=0, sticky="w", padx=8, pady=8)

        ttk.Radiobutton(
            self, text="DHCP (automatic – recommended)",
            variable=self._mode, value="dhcp",
            command=self._on_mode_change,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=2)

        ttk.Radiobutton(
            self, text="Static IP",
            variable=self._mode, value="static",
            command=self._on_mode_change,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=2)

        # Static IP fields (hidden initially)
        self._static_frame = ttk.Frame(self)
        self._static_frame.columnconfigure(1, weight=1)

        self.ip_address = StringVar(value="192.168.1.100")
        self.netmask = StringVar(value="255.255.255.0")
        self.gateway = StringVar(value="192.168.1.1")
        self.dns_servers = StringVar(value="8.8.8.8 8.8.4.4")
        self.interface = StringVar(value="eth0")

        fields = [
            ("Interface:", self.interface),
            ("IP Address:", self.ip_address),
            ("Netmask:", self.netmask),
            ("Gateway:", self.gateway),
            ("DNS Servers:", self.dns_servers),
        ]
        for i, (lbl_text, var) in enumerate(fields):
            _label(self._static_frame, lbl_text, i)
            _entry(self._static_frame, var, i, width=30)

        self._static_frame.grid(
            row=3, column=0, columnspan=3, padx=16, pady=8, sticky="ew"
        )
        self._on_mode_change()

    def _on_mode_change(self) -> None:
        for child in self._static_frame.winfo_children():
            if self._mode.get() == "static":
                child.configure(state="normal")
            else:
                try:
                    child.configure(state="disabled")
                except tk.TclError:
                    pass

    def get_network_config(self) -> dict | None:
        if self._mode.get() == "dhcp":
            return None  # cloud-init defaults to DHCP

        iface = self.interface.get().strip() or "eth0"
        ip = self.ip_address.get().strip()
        nm = self.netmask.get().strip()
        gw = self.gateway.get().strip()
        dns = [s.strip() for s in self.dns_servers.get().split() if s.strip()]

        return {
            "version": 2,
            "ethernets": {
                iface: {
                    "addresses": [f"{ip}/{_netmask_to_prefix(nm)}"],
                    "gateway4": gw,
                    "nameservers": {"addresses": dns},
                }
            },
        }


def _netmask_to_prefix(netmask: str) -> int:
    """Convert dotted-decimal netmask to CIDR prefix length."""
    try:
        return sum(bin(int(octet)).count("1") for octet in netmask.split("."))
    except (ValueError, AttributeError):
        return 24


class PackagesTab(ttk.Frame):
    """Tab for packages to install and run commands."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)

        self.pkg_update = BooleanVar(value=True)
        self.pkg_upgrade = BooleanVar(value=False)

        options_frame = ttk.LabelFrame(self, text="Package management")
        options_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Checkbutton(options_frame, text="Run apt update on first boot",
                        variable=self.pkg_update).pack(anchor="w", padx=8, pady=2)
        ttk.Checkbutton(options_frame, text="Run apt upgrade on first boot",
                        variable=self.pkg_upgrade).pack(anchor="w", padx=8, pady=2)

        pkg_frame = ttk.LabelFrame(self, text="Packages to install (one per line)")
        pkg_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        pkg_frame.columnconfigure(0, weight=1)
        pkg_frame.rowconfigure(0, weight=1)
        self.packages = scrolledtext.ScrolledText(pkg_frame, width=50, height=6)
        self.packages.pack(fill="both", expand=True, padx=4, pady=4)

        cmd_frame = ttk.LabelFrame(self, text="Run commands on first boot (one per line)")
        cmd_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        cmd_frame.columnconfigure(0, weight=1)
        cmd_frame.rowconfigure(0, weight=1)
        self.runcmd = scrolledtext.ScrolledText(cmd_frame, width=50, height=6)
        self.runcmd.pack(fill="both", expand=True, padx=4, pady=4)

        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)


class PreviewTab(ttk.Frame):
    """Tab showing the generated YAML content."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)

        inner = ttk.Notebook(self)
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        self._ud_text = self._make_text_tab(inner, " user-data ")
        self._md_text = self._make_text_tab(inner, " meta-data ")
        self._nc_text = self._make_text_tab(inner, " network-config ")

    @staticmethod
    def _make_text_tab(nb: ttk.Notebook, label: str) -> scrolledtext.ScrolledText:
        frame = ttk.Frame(nb)
        nb.add(frame, text=label)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        txt = scrolledtext.ScrolledText(frame, font=("Courier", 10))
        txt.pack(fill="both", expand=True)
        return txt

    def set_content(
        self, user_data: str, meta_data: str, network_config: str | None
    ) -> None:
        for widget, content in [
            (self._ud_text, user_data),
            (self._md_text, meta_data),
            (self._nc_text, network_config or "(not configured – DHCP will be used)"),
        ]:
            widget.configure(state="normal")
            widget.delete("1.0", END)
            widget.insert("1.0", content)
            widget.configure(state="disabled")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
