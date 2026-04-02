"""
app.py
GUI front-end for preparing a Canonical Ubuntu Server .ova to run with cloud-init.

Supports two delivery modes selectable at the top of the window:

  ESXi / VMware (guestinfo)
      Cloud-init payloads are base64-encoded and embedded directly in the OVF
      descriptor as VMware guestinfo properties.  ESXi reads them via
      open-vm-tools at boot — no separate ISO or CDROM required.  The SHA-256
      manifest is regenerated so ESXi accepts the modified OVA.

  Universal (NoCloud seed ISO)
      A seed ISO labelled "cidata" is built and attached to the OVA as a
      virtual CDROM.  Works with VirtualBox, Proxmox (KVM/QEMU), and any
      hypervisor that supports the cloud-init NoCloud datasource.

Usage:
    python app.py
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import traceback
from pathlib import Path
from tkinter import (
    BooleanVar,
    END,
    StringVar,
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
from guestinfo_handler import apply_guestinfo_to_ova
from iso_builder import build_seed_iso
from ova_downloader import UBUNTU_VERSIONS, download_ova
from ova_handler import apply_seed_iso_to_ova


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def _label(parent: tk.Widget, text: str, row: int, col: int = 0,
           **kw) -> ttk.Label:
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
# Delivery Mode info dialog
# ---------------------------------------------------------------------------

class DeliveryModeInfoDialog(tk.Toplevel):
    """
    Modal dialog that explains the two deployment modes and helps the
    user choose the right one.  Shown automatically on first launch and
    available at any time via the '? About modes' button.
    """

    _MODES = [
        (
            "guestinfo",
            "ESXi / VMware  —  guestinfo  (Recommended)",
            (
                "Cloud-init config is embedded directly in the OVF descriptor\n"
                "as base64-encoded VMware guestinfo properties.\n\n"
                "✔  ESXi 7  —  primary target\n"
                "✔  VMware Workstation / Fusion\n"
                "✔  Proxmox with open-vm-tools  (emerging support)\n"
                "✔  No separate ISO file required\n"
                "✔  Config survives snapshots and VM clones\n"
                "✔  SHA-256 manifest regenerated for ESXi integrity check"
            ),
        ),
        (
            "nocloud",
            "Universal  —  NoCloud seed ISO",
            (
                "A small ISO (volume label: 'cidata') is created and\n"
                "attached to the OVA as a virtual CDROM drive.\n\n"
                "✔  VirtualBox\n"
                "✔  Proxmox  —  KVM / QEMU  (native, no extra tools)\n"
                "✔  Any hypervisor supporting the NoCloud datasource\n"
                "⚠  ISO must remain attached to the VM\n"
                "⚠  May need re-injection after OVA re-import"
            ),
        ),
    ]

    def __init__(self, parent: tk.Widget, current_mode: str) -> None:
        super().__init__(parent)
        self.title("Deployment Mode — What Should I Choose?")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg="#1a73e8")
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="  Choose Your Deployment Target",
            bg="#1a73e8",
            fg="white",
            font=("Helvetica", 13, "bold"),
            pady=12,
        ).pack(side="left")

        # ── Mode cards ────────────────────────────────────────────────────
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        for mode_key, title, desc in self._MODES:
            is_current = (mode_key == current_mode)
            card = tk.LabelFrame(
                body,
                text=f"  {'●' if is_current else '○'}  {title}",
                fg="#1a73e8" if is_current else "#444444",
                font=("Helvetica", 10, "bold" if is_current else "normal"),
                bd=2 if is_current else 1,
                relief="solid" if is_current else "groove",
                padx=12,
                pady=8,
            )
            card.pack(fill="x", pady=(0, 10))

            if is_current:
                ttk.Label(
                    card,
                    text="★  Currently selected",
                    foreground="#1a73e8",
                    font=("Helvetica", 9, "italic"),
                ).pack(anchor="w")

            ttk.Label(
                card,
                text=desc,
                justify="left",
                font=("Helvetica", 9),
            ).pack(anchor="w", pady=(4, 0))

        # ── Proxmox / Broadcom advisory ───────────────────────────────────
        note = tk.Frame(body, bg="#fff3cd", bd=1, relief="solid")
        note.pack(fill="x", pady=(0, 14))

        tk.Label(
            note,
            text="⚠  Proxmox migration note",
            bg="#fff3cd",
            font=("Helvetica", 9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 2))

        tk.Label(
            note,
            text=(
                "As Broadcom tightens ESXi licensing, Proxmox is a natural migration path.\n"
                "Both modes work on Proxmox — guestinfo via open-vm-tools,\n"
                "NoCloud ISO natively via KVM/QEMU — so switching later is low-risk."
            ),
            bg="#fff3cd",
            font=("Helvetica", 9),
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 10))

        ttk.Button(body, text="Got it", command=self.destroy, width=12).pack()

        # Center over parent
        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width()  - self.winfo_width())  // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")
        self.wait_window(self)


# ---------------------------------------------------------------------------
# Main Application Window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("cloud-init GUI  –  Ubuntu Server OVA Configurator")
        self.resizable(True, True)
        self.minsize(760, 640)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Shared delivery mode state
        self._delivery_mode = StringVar(value="guestinfo")

        # Thread → UI message queue
        self._q: queue.Queue = queue.Queue()

        # ── Delivery Mode selector (top of window) ────────────────────────
        self._build_delivery_mode_frame()

        # ── Tabbed notebook ───────────────────────────────────────────────
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self._tab_ova      = OvaTab(self._notebook,
                                    log_fn=self._enqueue_log,
                                    progress_fn=self._enqueue_progress)
        self._tab_instance = InstanceTab(self._notebook)
        self._tab_user     = UserTab(self._notebook)
        self._tab_network  = NetworkTab(self._notebook)
        self._tab_packages = PackagesTab(self._notebook)
        self._tab_preview  = PreviewTab(self._notebook)

        for tab, label in [
            (self._tab_ova,      " OVA File "),
            (self._tab_instance, " Instance "),
            (self._tab_user,     " User Account "),
            (self._tab_network,  " Network "),
            (self._tab_packages, " Packages & Commands "),
            (self._tab_preview,  " Preview "),
        ]:
            self._notebook.add(tab, text=label)

        # ── Bottom button bar ─────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=(0, 4))

        self._btns: dict[str, ttk.Button] = {}
        for key, text, cmd in [
            ("preview",  "Preview YAML",     self._on_preview),
            ("saveyaml", "Save YAML files…", self._on_save_yaml),
            ("buildiso", "Build seed ISO…",  self._on_build_iso),
            ("applyova", "Apply to OVA…",    self._on_apply_to_ova),
        ]:
            btn = ttk.Button(btn_frame, text=text, command=cmd)
            btn.pack(side="left", padx=4)
            self._btns[key] = btn

        self._status = StringVar(value="Ready.")
        ttk.Label(btn_frame, textvariable=self._status,
                  foreground="gray").pack(side="right", padx=8)

        # ── Progress bar (hidden until a build starts) ────────────────────
        self._progress_var  = tk.DoubleVar(value=0)
        self._prog_frame    = ttk.Frame(self)
        self._prog_frame.pack(fill="x", padx=8, pady=(0, 2))
        self._progress_bar  = ttk.Progressbar(
            self._prog_frame, variable=self._progress_var, maximum=100,
        )
        self._progress_bar.pack(fill="x")
        self._prog_frame.pack_forget()   # shown on demand

        # ── Log pane toggle ───────────────────────────────────────────────
        self._log_shown = False
        log_bar = ttk.Frame(self)
        log_bar.pack(fill="x", padx=8, pady=(0, 2))
        self._log_toggle_btn = ttk.Button(
            log_bar,
            text="▶  Show Build Log",
            command=self._toggle_log,
            width=18,
        )
        self._log_toggle_btn.pack(side="left")

        self._log_outer = ttk.LabelFrame(self, text="Build Log", padding=4)
        self._log_text  = scrolledtext.ScrolledText(
            self._log_outer,
            height=10,
            state="disabled",
            font=("Courier", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
        )
        self._log_text.pack(fill="both", expand=True)

        # ── Wire up delivery mode trace + set initial button states ───────
        self._delivery_mode.trace_add("write", lambda *_: self._on_mode_changed())
        self._on_mode_changed()

        # ── Start queue polling ───────────────────────────────────────────
        self.after(100, self._poll_queue)

        # ── Show delivery mode info dialog on startup (400 ms delay so the
        #    main window renders first) ────────────────────────────────────
        self.after(400, lambda: DeliveryModeInfoDialog(self, self._delivery_mode.get()))

    # ------------------------------------------------------------------
    # Delivery Mode frame
    # ------------------------------------------------------------------

    def _build_delivery_mode_frame(self) -> None:
        dm_frame = ttk.LabelFrame(self, text="Delivery Mode", padding=(8, 6))
        dm_frame.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Radiobutton(
            dm_frame,
            text="ESXi / VMware  (guestinfo — recommended)",
            variable=self._delivery_mode,
            value="guestinfo",
        ).pack(side="left", padx=(0, 20))

        ttk.Radiobutton(
            dm_frame,
            text="Universal  (NoCloud seed ISO)",
            variable=self._delivery_mode,
            value="nocloud",
        ).pack(side="left", padx=(0, 20))

        ttk.Button(
            dm_frame,
            text="?  About modes",
            command=lambda: DeliveryModeInfoDialog(self, self._delivery_mode.get()),
            width=14,
        ).pack(side="left")

    def _on_mode_changed(self) -> None:
        """Grey out 'Build seed ISO' in guestinfo mode — it's not needed."""
        if not hasattr(self, "_btns"):
            return
        is_esxi = self._delivery_mode.get() == "guestinfo"
        self._btns["buildiso"].configure(
            state="disabled" if is_esxi else "normal",
        )

    # ------------------------------------------------------------------
    # Log pane
    # ------------------------------------------------------------------

    def _toggle_log(self) -> None:
        if self._log_shown:
            self._log_outer.pack_forget()
            self._log_shown = False
            self._log_toggle_btn.configure(text="▶  Show Build Log")
        else:
            self._log_outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            self._log_shown = True
            self._log_toggle_btn.configure(text="▼  Hide Build Log")

    def _enqueue_log(self, text: str) -> None:
        """Thread-safe: route a log message through the queue."""
        self._q.put(("log", text))

    def _enqueue_progress(self, pct: int, message: str) -> None:
        """Thread-safe: route a progress update through the queue."""
        self._q.put(("progress", pct, message))

    def _poll_queue(self) -> None:
        """Drain the worker → UI message queue (runs every 100 ms)."""
        try:
            while True:
                msg = self._q.get_nowait()

                if msg[0] == "log":
                    self._append_log(msg[1])

                elif msg[0] == "progress":
                    _, pct, status = msg
                    if pct >= 0:
                        self._prog_frame.pack(fill="x", padx=8, pady=(0, 2))
                        self._progress_var.set(pct)
                        if pct >= 100:
                            # Auto-hide progress bar after a short pause
                            self.after(1800, self._prog_frame.pack_forget)
                    self._status.set(status)

                elif msg[0] == "done":
                    _, success, detail = msg
                    if success:
                        messagebox.showinfo("Success", detail)
                    else:
                        messagebox.showerror("Error", detail)

        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def _append_log(self, text: str) -> None:
        """Append *text* to the log pane; auto-show pane on first entry."""
        self._log_text.configure(state="normal")
        self._log_text.insert(END, text + "\n")
        self._log_text.see(END)
        self._log_text.configure(state="disabled")
        if not self._log_shown:
            self._toggle_log()

    # ------------------------------------------------------------------
    # Config builder
    # ------------------------------------------------------------------

    def _build_config(self) -> CloudInitConfig | None:
        """Collect and validate settings from all tabs.
        Returns a populated CloudInitConfig or None on validation failure.
        """
        cfg = CloudInitConfig()

        # Instance
        cfg.hostname    = self._tab_instance.hostname.get().strip()
        cfg.instance_id = self._tab_instance.instance_id.get().strip() or "cloud-init-gui"
        err = validate_hostname(cfg.hostname)
        if err:
            messagebox.showerror("Validation Error", f"Hostname: {err}")
            return None

        # User account
        cfg.username = self._tab_user.username.get().strip()
        err = validate_username(cfg.username)
        if err:
            messagebox.showerror("Validation Error", f"Username: {err}")
            return None

        cfg.password    = self._tab_user.password.get()
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
        cfg.package_update  = self._tab_packages.pkg_update.get()
        cfg.package_upgrade = self._tab_packages.pkg_upgrade.get()
        raw_pkgs = self._tab_packages.packages.get("1.0", END).strip()
        cfg.packages = [p.strip() for p in raw_pkgs.splitlines() if p.strip()]
        for pkg in cfg.packages:
            err = validate_package_name(pkg)
            if err:
                messagebox.showerror("Validation Error", err)
                return None

        raw_cmd  = self._tab_packages.runcmd.get("1.0", END).strip()
        cfg.runcmd = [c for c in raw_cmd.splitlines() if c.strip()]

        return cfg

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_preview(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return
        self._tab_preview.set_content(
            generate_user_data(cfg),
            generate_meta_data(cfg),
            generate_network_config(cfg),
        )
        self._notebook.select(self._tab_preview)

    def _on_save_yaml(self) -> None:
        cfg = self._build_config()
        if cfg is None:
            return

        directory = filedialog.askdirectory(title="Choose output folder")
        if not directory:
            return

        out = Path(directory)
        ud  = generate_user_data(cfg)
        md  = generate_meta_data(cfg)
        nc  = generate_network_config(cfg)

        (out / "user-data").write_text(ud, encoding="utf-8")
        (out / "meta-data").write_text(md, encoding="utf-8")
        if nc is not None:
            (out / "network-config").write_text(nc, encoding="utf-8")

        self._status.set(f"Saved to {out}")
        messagebox.showinfo("Saved", f"cloud-init files saved to:\n{out}")

    def _on_build_iso(self) -> None:
        """Build a standalone NoCloud seed ISO (NoCloud mode only)."""
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
        except Exception as exc:
            self._status.set("Error building ISO.")
            messagebox.showerror("Error", f"Failed to build ISO:\n{exc}")

    def _on_apply_to_ova(self) -> None:
        """Apply cloud-init config to the OVA using the selected delivery mode."""
        cfg = self._build_config()
        if cfg is None:
            return

        ova_in = self._tab_ova.ova_path.get().strip()
        if not ova_in or not Path(ova_in).is_file():
            messagebox.showerror(
                "No OVA selected",
                "Please select or download a source OVA on the 'OVA File' tab.",
            )
            return

        mode         = self._delivery_mode.get()
        stem         = Path(ova_in).stem
        default_name = f"{stem}-esxi.ova" if mode == "guestinfo" else f"{stem}-cloud-init.ova"

        out_path_str = filedialog.asksaveasfilename(
            title="Save configured OVA as…",
            defaultextension=".ova",
            filetypes=[("OVA file", "*.ova"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not out_path_str:
            return

        out_path = Path(out_path_str)
        self._status.set("Working…")
        self._progress_var.set(0)
        self.update_idletasks()

        def _worker() -> None:
            try:
                ud = generate_user_data(cfg)
                md = generate_meta_data(cfg)
                nc = generate_network_config(cfg)

                self._enqueue_log("=" * 54)

                if mode == "guestinfo":
                    self._enqueue_log("Mode:   ESXi / VMware  (guestinfo)")
                    self._enqueue_log(f"Source: {Path(ova_in).name}")
                    self._enqueue_log(f"Output: {out_path.name}")
                    self._enqueue_log("")
                    self._enqueue_progress(10, "Extracting OVA…")
                    self._enqueue_log("Extracting OVA archive…")
                    self._enqueue_progress(30, "Injecting guestinfo into OVF…")
                    self._enqueue_log("Injecting guestinfo properties into OVF descriptor…")
                    apply_guestinfo_to_ova(ova_in, ud, md, str(out_path))
                    self._enqueue_progress(90, "Repacking OVA…")
                    self._enqueue_log("Regenerating SHA-256 manifest…")
                    self._enqueue_log("Repacking OVA (ESXi-safe ordering)…")
                    self._enqueue_progress(100, "Done.")
                    self._enqueue_log("")
                    self._enqueue_log(f"✔  guestinfo injection complete")
                    self._enqueue_log(f"✔  SHA-256 manifest regenerated")
                    self._enqueue_log(f"✔  Output: {out_path}")
                    self._enqueue_log("")
                    self._enqueue_log("Deploy via ESXi → Deploy OVF Template")
                    self._enqueue_log(f"SSH:  ssh {cfg.username}@<vm-ip>")
                    detail = (
                        f"OVA written to:\n{out_path}\n\n"
                        "Deploy via ESXi 'Deploy OVF Template'.\n"
                        f"SSH:  ssh {cfg.username}@<vm-ip>"
                    )

                else:  # nocloud
                    self._enqueue_log("Mode:   Universal  (NoCloud seed ISO)")
                    self._enqueue_log(f"Source: {Path(ova_in).name}")
                    self._enqueue_log(f"Output: {out_path.name}")
                    self._enqueue_log("")
                    self._enqueue_progress(10, "Building seed ISO…")
                    self._enqueue_log("Building cidata seed ISO…")
                    with tempfile.TemporaryDirectory(prefix="cloud-init-gui-") as tmp:
                        iso_path = Path(tmp) / "seed.iso"
                        build_seed_iso(ud, md, str(iso_path), network_config=nc)
                        self._enqueue_progress(45, "Patching OVF descriptor…")
                        self._enqueue_log("Injecting CDROM drive into OVF…")
                        apply_seed_iso_to_ova(ova_in, str(iso_path), str(out_path))
                    self._enqueue_progress(100, "Done.")
                    self._enqueue_log("")
                    self._enqueue_log(f"✔  NoCloud seed ISO injected")
                    self._enqueue_log(f"✔  Output: {out_path}")
                    self._enqueue_log("")
                    self._enqueue_log("Import into VirtualBox / Proxmox / VMware.")
                    detail = (
                        f"OVA written to:\n{out_path}\n\n"
                        "Import into VirtualBox, Proxmox, or VMware.\n"
                        "The attached CDROM provides cloud-init on first boot."
                    )

                self._q.put(("done", True, detail))

            except Exception as exc:
                tb = traceback.format_exc()
                self._enqueue_log(f"\n✗  ERROR: {exc}")
                self._enqueue_log(tb)
                self._enqueue_progress(0, "Error.")
                self._q.put(("done", False, f"Build failed:\n{exc}"))

        threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Tab widgets
# ---------------------------------------------------------------------------

class OvaTab(ttk.Frame):
    """Tab for selecting a local OVA or downloading one from Canonical."""

    def __init__(
        self,
        parent: ttk.Notebook,
        log_fn=None,
        progress_fn=None,
    ) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)

        self.ova_path    = StringVar()
        self._log_fn     = log_fn      or (lambda msg: None)
        self._progress_fn = progress_fn or (lambda pct, msg: None)
        self._source_mode = StringVar(value="local")

        # ── Source frame ───────────────────────────────────────────────────
        src = ttk.LabelFrame(self, text="OVA Source", padding=(10, 8))
        src.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 6))
        src.columnconfigure(1, weight=1)

        # Row 0 — local file
        ttk.Radiobutton(
            src, text="Local file:",
            variable=self._source_mode, value="local",
            command=self._on_source_toggle,
        ).grid(row=0, column=0, sticky="w", pady=4)

        self._local_entry = ttk.Entry(src, textvariable=self.ova_path, width=46)
        self._local_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        self._browse_btn = ttk.Button(src, text="Browse…", command=self._browse)
        self._browse_btn.grid(row=0, column=2, padx=(0, 4), pady=4)

        # Row 1 — download
        ttk.Radiobutton(
            src, text="Download from Canonical:",
            variable=self._source_mode, value="download",
            command=self._on_source_toggle,
        ).grid(row=1, column=0, sticky="w", pady=(8, 4))

        self._version_var = StringVar(value=list(UBUNTU_VERSIONS.keys())[0])
        self._version_cb  = ttk.Combobox(
            src,
            textvariable=self._version_var,
            values=list(UBUNTU_VERSIONS.keys()),
            state="disabled",
            width=44,
        )
        self._version_cb.grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 4))

        self._download_btn = ttk.Button(
            src, text="Download…",
            command=self._start_download,
            state="disabled",
        )
        self._download_btn.grid(row=1, column=2, padx=(0, 4), pady=(8, 4))

        # ── Info text ──────────────────────────────────────────────────────
        info = (
            "Select an existing Canonical Ubuntu Server .ova file, or download "
            "the latest directly from cloud-images.ubuntu.com.\n\n"
            "Tip: Ubuntu 24.04 LTS (Noble) is recommended for new deployments.\n"
            "If you have already downloaded an OVA, use 'Local file' to avoid "
            "a repeat download."
        )
        ttk.Label(
            self, text=info,
            justify="left",
            wraplength=560,
            foreground="#555555",
        ).grid(row=1, column=0, padx=12, pady=(4, 12), sticky="w")

    def _on_source_toggle(self) -> None:
        is_dl = self._source_mode.get() == "download"
        self._local_entry.configure( state="disabled" if is_dl else "normal")
        self._browse_btn.configure(  state="disabled" if is_dl else "normal")
        self._version_cb.configure(  state="readonly" if is_dl else "disabled")
        self._download_btn.configure(state="normal"   if is_dl else "disabled")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Ubuntu Server OVA",
            filetypes=[("OVA files", "*.ova"), ("All files", "*.*")],
        )
        if path:
            self.ova_path.set(path)

    def _start_download(self) -> None:
        version_name = self._version_var.get()
        version_info = UBUNTU_VERSIONS[version_name]
        url          = version_info["url"]
        codename     = version_info["codename"]

        dest_str = filedialog.asksaveasfilename(
            title="Save downloaded OVA as…",
            defaultextension=".ova",
            filetypes=[("OVA files", "*.ova")],
            initialfile=f"{codename}-server-cloudimg-amd64.ova",
        )
        if not dest_str:
            return

        dest_path = Path(dest_str)
        self._download_btn.configure(state="disabled", text="Downloading…")
        self._log_fn(f"Starting download: {version_name}")
        self._log_fn(f"URL: {url}")

        def _worker() -> None:
            try:
                download_ova(url, dest_path, progress_callback=self._progress_fn)
                self.ova_path.set(str(dest_path))
                self._log_fn(f"✔  Download complete: {dest_path}")
                self.after(0, lambda: self._download_btn.configure(
                    state="normal", text="Download…"
                ))
            except Exception as exc:
                self._log_fn(f"✗  Download failed: {exc}")
                self.after(0, lambda: self._download_btn.configure(
                    state="normal", text="Download…"
                ))
                self.after(0, lambda: messagebox.showerror(
                    "Download Failed", str(exc)
                ))

        threading.Thread(target=_worker, daemon=True).start()


class InstanceTab(ttk.Frame):
    """Tab for VM / instance metadata."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(1, weight=1)

        self.hostname    = StringVar(value="ubuntu-server")
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

        self.username    = StringVar(value="ubuntu")
        self.password    = StringVar()
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

        self._static_frame = ttk.Frame(self)
        self._static_frame.columnconfigure(1, weight=1)

        self.ip_address  = StringVar(value="192.168.1.100")
        self.netmask     = StringVar(value="255.255.255.0")
        self.gateway     = StringVar(value="192.168.1.1")
        self.dns_servers = StringVar(value="8.8.8.8 8.8.4.4")
        self.interface   = StringVar(value="eth0")

        for i, (lbl_text, var) in enumerate([
            ("Interface:", self.interface),
            ("IP Address:", self.ip_address),
            ("Netmask:",    self.netmask),
            ("Gateway:",    self.gateway),
            ("DNS Servers:", self.dns_servers),
        ]):
            _label(self._static_frame, lbl_text, i)
            _entry(self._static_frame, var, i, width=30)

        self._static_frame.grid(
            row=3, column=0, columnspan=3, padx=16, pady=8, sticky="ew"
        )
        self._on_mode_change()

    def _on_mode_change(self) -> None:
        for child in self._static_frame.winfo_children():
            try:
                child.configure(
                    state="normal" if self._mode.get() == "static" else "disabled"
                )
            except tk.TclError:
                pass

    def get_network_config(self) -> dict | None:
        if self._mode.get() == "dhcp":
            return None

        iface = self.interface.get().strip() or "eth0"
        ip    = self.ip_address.get().strip()
        nm    = self.netmask.get().strip()
        gw    = self.gateway.get().strip()
        dns   = [s.strip() for s in self.dns_servers.get().split() if s.strip()]

        return {
            "version": 2,
            "ethernets": {
                iface: {
                    "addresses": [f"{ip}/{_netmask_to_prefix(nm)}"],
                    "gateway4":  gw,
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
    """Tab for packages to install and first-boot run commands."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)

        self.pkg_update  = BooleanVar(value=True)
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
    """Tab showing generated YAML content (read-only)."""

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
        self,
        user_data: str,
        meta_data: str,
        network_config: str | None,
    ) -> None:
        for widget, content in [
            (self._ud_text, user_data),
            (self._md_text, meta_data),
            (self._nc_text, network_config or "(not configured — DHCP will be used)"),
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
