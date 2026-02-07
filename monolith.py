import pil_config

import base64
import configparser
import ctypes
import datetime
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk

import customtkinter as ctk
import requests
from CTkMessagebox import CTkMessagebox
from PIL import Image, ImageTk

# DPI Scaling (Should support Windows, MacOS, Linux (X11) and hopefully Wayland)
def get_dpi_scaling():
    scaling = 1.0
    try:
        if os.name == 'nt':
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            monitor = ctypes.windll.user32.MonitorFromPoint((0, 0), 2)
            dpi_x = ctypes.c_uint()
            ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), None)
            scaling = dpi_x.value / 96.0
        elif os.name == "darwin":
            from AppKit import NSScreen
            scaling = NSScreen.mainScreen().backingScaleFactor()
        elif os.name == "posix":
            if os.environ.get("XDG_SESSION_TYPE") == "x11":
                try:
                    from Xlib import display
                    d = display.Display()
                    screen = d.screen()
                    dpi = screen.width_in_millimeters / (screen.width_in_pixels / screen.width)
                    scaling = dpi / 96.0
                except Exception as e:
                    logging.warning(f"Failed to get X11 DPI scaling: {e}")
                    scaling = float(os.environ.get("GDK_SCALE", 1.0))
            else:
                scaling = float(os.environ.get("GDK_SCALE", 1.0))
    except Exception as e:
        logging.warning(f"Failed to get DPI scaling: {e}")
        scaling = 1.0
    return scaling

# Constants
APP_VERSION = "1.0.3"
UPDATE_VERSION_URL = "https://raw.githubusercontent.com/fl4te/monolith/refs/heads/main/version.txt"
DISABLED_DIR_NAME = "_disabled"
# JK2 = assets0,1,2,5 + (6 for the Aspyr macOS build?)
# JK2MV = assetsmv,assetsmv2
# JKA = assets0,1,2,3
PROTECTED_ASSETS = {
    "assets0.pk3", "assets1.pk3", "assets2.pk3",
    "assets3.pk3", "assets5.pk3", "assets6.pk3",
    "assetsmv.pk3", "assetsmv2.pk3"
}

# UI Colors
COLOR_PRIMARY = "#3a86ff"       # Blue
COLOR_SUCCESS = "#8338ec"       # Purple
COLOR_DANGER = "#ff006e"        # Pink
COLOR_WARNING = "#fb5607"       # Orange
COLOR_TEXT_DIM = "#a0a0a0"      # Gray
COLOR_TEXT_BRIGHT = "#ffffff"   # White
DARK_BG_COLOR = "#1a1a2e"       # Dark blue-gray
COLOR_SCROLL_TROUGH = "#16213e" # Dark navy
COLOR_SCROLL_THUMB = "#3a86ff"  # Blue
COLOR_SCROLL_ARROW = "#a0a0a0"  # Gray
COLOR_ACCENT = "#00d4ff"        # Cyan

# Config Directory Migration
def migrate_old_config(old_dir: Path, new_dir: Path):
    old_files = [
        old_dir / "config.json",
        old_dir / "servers.ini",
        old_dir / "error.log",
    ]
    new_dir.mkdir(parents=True, exist_ok=True)
    for old_file in old_files:
        if old_file.exists():
            shutil.copy2(old_file, new_dir / old_file.name)
    shutil.rmtree(old_dir, ignore_errors=True)

def get_config_dir(app_name: str = "monolith") -> Path:
    old_dir = get_config_dir_old("JK2ModManager")
    new_dir = get_config_dir_old(app_name)

    if old_dir.exists() and old_dir.is_dir():
        migrate_old_config(old_dir, new_dir)
        return new_dir
    else:
        new_dir.mkdir(parents=True, exist_ok=True)
        return new_dir

def get_config_dir_old(app_name: str) -> Path:
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / app_name
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
        return Path(appdata) / app_name
    else:
        return Path.home() / f".{app_name.lower()}"

CONFIG_DIR = get_config_dir("monolith")
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"
RCON_CONFIG_FILE = CONFIG_DIR / "servers.ini"

# Logging
logfile_path = CONFIG_DIR / "error.log"
logging.basicConfig(filename=logfile_path, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Utility Functions
def get_sha256_hash(filepath: Path) -> str:
    hash_obj = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(4096):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()
    except Exception as e:
        logging.error(f"Failed to get SHA256 hash for {filepath}: {e}")
        return "ERROR"

def clean_rcon_response(response: str) -> str:
    cleaned_response = response
    for i in range(8):
        cleaned_response = cleaned_response.replace(f"^{i}", "")
    lines = cleaned_response.split('\n')
    return '\n'.join(line.strip() for line in lines if line.strip())

# UI Components
class CTkTextbox(ctk.CTkTextbox):
    def __init__(self, master, **kwargs):
        super().__init__(master, wrap="word", **kwargs)

class CTkInputDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, prompt: str, initialvalue: str = ""):
        super().__init__(parent)
        self.title(title)
        self.prompt = prompt
        self.initialvalue = initialvalue
        self.user_input = None
        self.parent = parent
        self.transient(parent)
        width = 380
        height = 160
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.update_idletasks()
        self.wait_visibility()
        self.grab_set()
        self.focus_set()
        self.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(self, text=prompt, font=ctk.CTkFont(size=12))
        lbl.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")
        self.entry = ctk.CTkEntry(self, width=320, font=ctk.CTkFont(size=12), corner_radius=8)
        self.entry.insert(0, initialvalue)
        self.entry.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.entry.focus_set()

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")

        btn_ok = ctk.CTkButton(
            btn_frame, text="OK", width=80, command=self.on_ok,
            fg_color=COLOR_ACCENT, hover_color=COLOR_PRIMARY, corner_radius=8
        )
        btn_ok.pack(side="left", padx=(10, 0))

        btn_cancel = ctk.CTkButton(
            btn_frame, text="Cancel", width=80, command=self.on_cancel,
            fg_color=COLOR_SCROLL_TROUGH, hover_color=COLOR_SCROLL_THUMB, corner_radius=8
        )
        btn_cancel.pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Return>", lambda event: self.on_ok())
        self.bind("<Escape>", lambda event: self.on_cancel())
        self.wait_window(self)

    def on_ok(self):
        self.user_input = self.entry.get()
        self.destroy()

    def on_cancel(self):
        self.user_input = None
        self.destroy()

    def destroy(self):
        if self.grab_status():
            self.grab_release()
        super().destroy()

def ctk_ask_string(parent, title: str, prompt: str, initialvalue: str = "") -> str | None:
    dialog = CTkInputDialog(parent, title, prompt, initialvalue)
    return dialog.user_input

# Main Application
class JK2ModManager(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.mod_folder: Path | None = None
        self.game_exe_path: Path | None = None
        self.game_process: subprocess.Popen | None = None
        self.search_var = ctk.StringVar()
        self.path_var = ctk.StringVar()
        self.status_var = ctk.StringVar(value="Ready")
        self.active_profile: str | None = None
        self.profiles: dict[str, dict] = {}
        self.mod_index: dict[str, Path] = {}
        self.config = {}

        self.rcon_config = configparser.ConfigParser()
        if not os.path.exists(RCON_CONFIG_FILE):
            with open(RCON_CONFIG_FILE, 'w') as f:
                self.rcon_config.write(f)
        self.rcon_config.read(RCON_CONFIG_FILE)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.settimeout(5)

        self.title("MONOLITH MOD MANAGER")
        self.geometry("1000x700")
        self.minsize(1000, 700)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = (screen_w - 1000) // 2
        y = (screen_h - 700) // 2
        self.geometry(f"1000x700+{x}+{y}")

        config = self._load_config()
        self.config = config
        self.profiles = config.get("profiles", {})
        self.active_profile = config.get("active_profile", None)

        ctk.set_appearance_mode("Dark")

        if self.active_profile and self.active_profile in self.profiles:
            p = self.profiles[self.active_profile]
            self.game_exe_path = Path(p.get("game_exe")) if p.get("game_exe") else None
        else:
            self.game_exe_path = None

        if "geometry" in config:
            self.geometry(config["geometry"])

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.create_sidebar()
        self.create_main_area()
        self.create_context_menu()
        self.refresh_profile_dropdown()
        self.load_profile_folder()
        self.update_status()
        self.update_treeview_style("Dark")
        self.update_preview_style("Dark")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # Core UI
    def create_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=COLOR_SCROLL_TROUGH)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(14, weight=1)

        lbl_title = ctk.CTkLabel(
            self.sidebar, text="MONOLITH",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=COLOR_TEXT_BRIGHT
        )
        lbl_title.grid(row=0, column=0, padx=20, pady=(20, 5))

        lbl_subtitle = ctk.CTkLabel(
            self.sidebar, text="MOD MANAGER",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR_TEXT_DIM
        )
        lbl_subtitle.grid(row=1, column=0, padx=20, pady=(0, 20))

        lbl_params = ctk.CTkLabel(
            self.sidebar, text="Launch Parameters:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        lbl_params.grid(row=2, column=0, padx=20, pady=(0, 5), sticky="w")

        self.devmode_var = ctk.BooleanVar(value=False)
        self.devmode_checkbox = ctk.CTkCheckBox(
            self.sidebar, text="Developer Mode", variable=self.devmode_var,
            onvalue=True, offvalue=False, font=ctk.CTkFont(size=12),
            checkbox_height=18, checkbox_width=18
        )
        self.devmode_checkbox.grid(row=3, column=0, padx=20, pady=(5, 0), sticky="w")

        self.logfile_var = ctk.BooleanVar(value=False)
        self.logfile_checkbox = ctk.CTkCheckBox(
            self.sidebar, text="Logfile", variable=self.logfile_var,
            onvalue=True, offvalue=False, font=ctk.CTkFont(size=12),
            checkbox_height=18, checkbox_width=18
        )
        self.logfile_checkbox.grid(row=4, column=0, padx=20, pady=(5, 0), sticky="w")

        self.custom_params_var = ctk.StringVar()
        self.custom_params_entry = ctk.CTkEntry(
            self.sidebar, textvariable=self.custom_params_var,
            placeholder_text="Custom parameters...",
            font=ctk.CTkFont(size=12), height=30, corner_radius=8
        )
        self.custom_params_entry.grid(row=5, column=0, padx=20, pady=(5, 10), sticky="ew")

        self.btn_launch = ctk.CTkButton(
            self.sidebar, text="LAUNCH GAME", height=50,
            fg_color=COLOR_SUCCESS, hover_color="#6a2c70",
            font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
            command=self.start_game_threaded
        )
        self.btn_launch.grid(row=6, column=0, padx=20, pady=10)

        ctk.CTkLabel(
            self.sidebar, text="Profile:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=7, column=0, padx=20, pady=(20, 0), sticky="w")

        self.opt_profile = ctk.CTkOptionMenu(
            self.sidebar, dynamic_resizing=False, command=self.change_profile_event,
            font=ctk.CTkFont(size=12), height=30, corner_radius=8
        )
        self.opt_profile.grid(row=8, column=0, padx=20, pady=(5, 10))

        p_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        p_frame.grid(row=9, column=0, padx=20, pady=5)

        ctk.CTkButton(
            p_frame, text="+", width=40, command=self.create_profile,
            fg_color=COLOR_SUCCESS, hover_color=COLOR_SCROLL_THUMB, corner_radius=8
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            p_frame, text="✎", width=40, command=self.rename_profile,
            fg_color=COLOR_PRIMARY, hover_color=COLOR_ACCENT, corner_radius=8
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            p_frame, text="🗑", width=40, command=self.delete_profile,
            fg_color=COLOR_DANGER, hover_color=COLOR_WARNING, corner_radius=8
        ).pack(side="left", padx=2)

        self.btn_check_updates = ctk.CTkButton(
            self.sidebar, text="Check for Updates",
            fg_color=DARK_BG_COLOR, hover_color=COLOR_PRIMARY,
            font=ctk.CTkFont(size=12), height=30, corner_radius=8,
            command=self.check_for_updates_threaded
        )
        self.btn_check_updates.grid(row=14, column=0, padx=20, pady=(0, 20), sticky="ew")

    def create_main_area(self):
        main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self.notebook = ctk.CTkTabview(
            main_frame, segmented_button_fg_color=COLOR_SCROLL_TROUGH,
            segmented_button_selected_color=COLOR_ACCENT, corner_radius=8
        )
        self.notebook.pack(fill="both", expand=True)

        self.mod_tab = self.notebook.add("Mod Manager")
        self.download_tab = self.notebook.add("Download Mods")
        self.rcon_tab = self.notebook.add("RCON Console")

        self.create_mod_tab()
        self.create_download_tab()
        self.create_rcon_tab()

    def create_mod_tab(self):
        top_bar = ctk.CTkFrame(self.mod_tab, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkButton(
            top_bar, text="📁 Base Folder", width=100, command=self.browse_folder,
            font=ctk.CTkFont(size=12), corner_radius=8
        ).pack(side="left", padx=(0, 10))

        self.entry_path = ctk.CTkEntry(
            top_bar, textvariable=self.path_var,
            placeholder_text="No base folder selected...", state="readonly",
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.entry_path.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            top_bar, text="Open", width=60, fg_color=COLOR_SCROLL_TROUGH,
            hover_color=COLOR_SCROLL_THUMB, command=self.open_in_explorer,
            font=ctk.CTkFont(size=12), corner_radius=8
        ).pack(side="right")

        search_bar = ctk.CTkFrame(self.mod_tab, fg_color="transparent")
        search_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            search_bar, text="Search Mods:",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left", padx=(0, 10))

        self.entry_search = ctk.CTkEntry(
            search_bar, textvariable=self.search_var,
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.entry_search.pack(side="left", fill="x", expand=True)
        self.entry_search.bind("<KeyRelease>", lambda e: self.refresh_list())

        ctk.CTkButton(
            search_bar, text="Export List", width=90,
            fg_color=COLOR_SCROLL_TROUGH, hover_color=COLOR_SCROLL_THUMB,
            command=self.export_json, font=ctk.CTkFont(size=12), corner_radius=8
        ).pack(side="right", padx=(10, 0))

        self.content_container = ctk.CTkFrame(self.mod_tab, fg_color="transparent")
        self.content_container.pack(fill="both", expand=True, pady=(0, 10))
        self.content_container.grid_columnconfigure(0, weight=3)
        self.content_container.grid_columnconfigure(1, weight=1)
        self.content_container.grid_rowconfigure(0, weight=1)

        self.tree_frame = ctk.CTkFrame(
            self.content_container, fg_color=COLOR_SCROLL_TROUGH, corner_radius=8
        )
        self.tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.tree_scroll = ttk.Scrollbar(self.tree_frame, style="Custom.Vertical.TScrollbar")
        self.tree_scroll.pack(side="right", fill="y")

        self.tree = ttk.Treeview(
            self.tree_frame, columns=("size", "status", "priority"),
            show="tree headings", selectmode="extended",
            yscrollcommand=self.tree_scroll.set
        )
        self.tree_scroll.config(command=self.tree.yview)

        self.tree.column("#0", width=0, stretch=tk.NO)
        self.tree.config(displaycolumns=("size", "status", "priority"))
        self.tree.heading("size", text="Size", anchor="w")
        self.tree.heading("status", text="State", anchor="w")
        self.tree.heading("priority", text="Filename (Load Order)", anchor="w")
        self.tree.pack(fill="both", expand=True, padx=2, pady=2)

        self.preview_frame = ctk.CTkFrame(
            self.content_container, fg_color=COLOR_SCROLL_TROUGH, corner_radius=8
        )
        self.preview_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.lbl_preview_title = ctk.CTkLabel(
            self.preview_frame, text="Mod Preview",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.lbl_preview_title.pack(pady=(10, 5))

        self.preview_box = ctk.CTkFrame(
            self.preview_frame, fg_color="#16213e", corner_radius=8
        )
        self.preview_box.pack(fill="both", expand=True, padx=10, pady=10)
        self.preview_box.pack_propagate(False)

        self.preview_canvas = ctk.CTkLabel(
            self.preview_box, text="No Preview", text_color=COLOR_TEXT_DIM,
            font=ctk.CTkFont(size=12)
        )
        self.preview_canvas.pack(fill="both", expand=True)

        action_bar = ctk.CTkFrame(self.mod_tab, fg_color="transparent")
        action_bar.pack(fill="x", pady=(10, 0))

        self.btn_install = ctk.CTkButton(
            action_bar, text="Install", command=self.install_mods_threaded,
            fg_color=COLOR_PRIMARY, hover_color="#2a68d3", corner_radius=8,
            font=ctk.CTkFont(size=12)
        )
        self.btn_install.pack(side="left", padx=(0, 10))

        self.btn_delete_mod = ctk.CTkButton(
            action_bar, text="Remove", fg_color=COLOR_DANGER,
            hover_color="#ff006e", command=self.delete_selected_threaded,
            corner_radius=8, font=ctk.CTkFont(size=12)
        )
        self.btn_delete_mod.pack(side="left", padx=(0, 10))

        self.btn_enable = ctk.CTkButton(
            action_bar, text="Enable Selected", fg_color=COLOR_SUCCESS,
            hover_color="#6a2c70", command=lambda: self.toggle_selected_mods_and_status("enable"),
            corner_radius=8, font=ctk.CTkFont(size=12)
        )
        self.btn_enable.pack(side="left", padx=(0, 10))

        self.btn_disable = ctk.CTkButton(
            action_bar, text="Disable Selected", fg_color=COLOR_WARNING,
            hover_color="#d65a31", command=lambda: self.toggle_selected_mods_and_status("disable"),
            corner_radius=8, font=ctk.CTkFont(size=12)
        )
        self.btn_disable.pack(side="left", padx=(0, 10))

        self.btn_refresh = ctk.CTkButton(
            action_bar, text="⟳ Refresh", width=90,
            fg_color=COLOR_SCROLL_TROUGH, hover_color=COLOR_SCROLL_THUMB,
            command=self.refresh_list, corner_radius=8, font=ctk.CTkFont(size=12)
        )
        self.btn_refresh.pack(side="right")

        self.lbl_status = ctk.CTkLabel(
            self.mod_tab, textvariable=self.status_var, anchor="w",
            text_color=COLOR_TEXT_DIM, font=ctk.CTkFont(size=12)
        )
        self.lbl_status.pack(fill="x", pady=(5, 0))

        self.tree.bind("<<TreeviewSelect>>", self.on_mod_selected)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Double-1>", lambda e: self.toggle_selected_mods_and_status())

    def create_download_tab(self):
        self.download_frame = ctk.CTkFrame(self.download_tab, fg_color="transparent")
        self.download_frame.pack(fill="both", expand=True, padx=20, pady=20)

        top_bar = ctk.CTkFrame(self.download_frame, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, 10))

        self.download_search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            top_bar, textvariable=self.download_search_var,
            placeholder_text="Search Mods...",
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        search_entry.bind("<KeyRelease>", lambda e: self.refresh_download_list())

        self.btn_refresh_downloads = ctk.CTkButton(
            top_bar, text="Refresh List", command=self.refresh_download_list,
            fg_color=COLOR_SCROLL_TROUGH, hover_color=COLOR_SCROLL_THUMB,
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.btn_refresh_downloads.pack(side="right")

        self.download_tree_frame = ctk.CTkFrame(
            self.download_frame, fg_color=COLOR_SCROLL_TROUGH, corner_radius=8
        )
        self.download_tree_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.download_tree_scroll = ttk.Scrollbar(
            self.download_tree_frame, style="Custom.Vertical.TScrollbar"
        )
        self.download_tree_scroll.pack(side="right", fill="y")

        self.download_tree = ttk.Treeview(
            self.download_tree_frame,
            columns=("name", "author", "size", "category", "uploader", "date", "preview"),
            show="headings", yscrollcommand=self.download_tree_scroll.set
        )
        self.download_tree_scroll.config(command=self.download_tree.yview)
        self.download_tree.pack(fill="both", expand=True)

        self.download_tree.heading("name", text="Name")
        self.download_tree.heading("author", text="Author")
        self.download_tree.heading("size", text="Size")
        self.download_tree.heading("category", text="Category")
        self.download_tree.heading("uploader", text="Uploader")
        self.download_tree.heading("date", text="Date")
        self.download_tree.heading("preview", text="Preview")

        self.download_tree.column("name", width=150, anchor="center")
        self.download_tree.column("author", width=120, anchor="center")
        self.download_tree.column("size", width=80, anchor="center")
        self.download_tree.column("category", width=100, anchor="center")
        self.download_tree.column("uploader", width=100, anchor="center")
        self.download_tree.column("date", width=80, anchor="center")
        self.download_tree.column("preview", width=80, anchor="center")

        self.download_progress_frame = ctk.CTkFrame(
            self.download_frame, fg_color="transparent"
        )
        self.download_progress_frame.pack(fill="x", pady=(10, 0))

        self.download_progress = ctk.CTkProgressBar(
            self.download_progress_frame, height=8, corner_radius=8
        )
        self.download_progress.pack(fill="x", padx=20, pady=5)
        self.download_progress.set(0)

        self.download_progress_percent = ctk.CTkLabel(
            self.download_progress_frame, text="0%", text_color=COLOR_TEXT_DIM,
            font=ctk.CTkFont(size=12)
        )
        self.download_progress_percent.pack(pady=5)

        action_bar = ctk.CTkFrame(self.download_frame, fg_color="transparent")
        action_bar.pack(fill="x", pady=(10, 0))

        self.btn_download_selected = ctk.CTkButton(
            action_bar, text="Download Selected", command=self.download_selected_mods,
            fg_color=COLOR_PRIMARY, hover_color="#2a68d3", corner_radius=8,
            font=ctk.CTkFont(size=12)
        )
        self.btn_download_selected.pack(side="right")

        self.download_preview_frame = ctk.CTkFrame(
            self.download_frame, fg_color="transparent"
        )
        self.download_preview_frame.pack(fill="x", pady=(10, 0))

        self.lbl_download_preview = ctk.CTkLabel(
            self.download_preview_frame, text="Preview:",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.lbl_download_preview.pack(side="top", pady=(0, 5))

        self.download_preview_canvas = ctk.CTkLabel(
            self.download_preview_frame, text="No Preview",
            text_color=COLOR_TEXT_DIM, width=200, height=100
        )
        self.download_preview_canvas.pack()

        self.download_tree.bind("<<TreeviewSelect>>", self.on_download_mod_selected)

    def create_rcon_tab(self):
        self.rcon_tab.grid_columnconfigure(0, weight=1)
        self.rcon_tab.grid_rowconfigure(7, weight=1)

        connection_frame = ctk.CTkFrame(self.rcon_tab, fg_color="transparent")
        connection_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        connection_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            connection_frame, text="Server Name:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, padx=5, pady=(0, 2), sticky="w")

        self.rcon_server_name_entry = ctk.CTkEntry(
            connection_frame, font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_server_name_entry.grid(row=1, column=0, padx=5, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(
            connection_frame, text="Server IP:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=0, padx=5, pady=(0, 2), sticky="w")

        self.rcon_server_ip_entry = ctk.CTkEntry(
            connection_frame, font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_server_ip_entry.grid(row=3, column=0, padx=5, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(
            connection_frame, text="Server Port:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=4, column=0, padx=5, pady=(0, 2), sticky="w")

        self.rcon_server_port_entry = ctk.CTkEntry(
            connection_frame, font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_server_port_entry.grid(row=5, column=0, padx=5, pady=(0, 10), sticky="ew")

        ctk.CTkLabel(
            connection_frame, text="RCON Password:", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=6, column=0, padx=5, pady=(0, 2), sticky="w")

        self.rcon_password_entry = ctk.CTkEntry(
            connection_frame, show="*", font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_password_entry.grid(row=7, column=0, padx=5, pady=(0, 10), sticky="ew")

        self.rcon_output_text = CTkTextbox(self.rcon_tab, font=ctk.CTkFont(size=12))
        self.rcon_output_text.grid(row=8, column=0, padx=20, pady=(0, 10), sticky="nsew")

        input_frame = ctk.CTkFrame(self.rcon_tab, fg_color="transparent")
        input_frame.grid(row=9, column=0, padx=20, pady=(0, 10), sticky="ew")
        input_frame.grid_columnconfigure(0, weight=1)

        self.rcon_input_entry = ctk.CTkEntry(
            input_frame, placeholder_text="Enter RCON command...",
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_input_entry.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="ew")
        self.rcon_input_entry.bind("<Return>", self.rcon_send_on_enter)

        self.rcon_send_button = ctk.CTkButton(
            input_frame, text="Send", command=self.rcon_send_command,
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_send_button.grid(row=0, column=1, padx=0, pady=0)

        server_mgmt_frame = ctk.CTkFrame(self.rcon_tab, fg_color="transparent")
        server_mgmt_frame.grid(row=10, column=0, padx=20, pady=(0, 20), sticky="ew")
        server_mgmt_frame.grid_columnconfigure(0, weight=1)

        self.rcon_saved_servers_combobox = ctk.CTkComboBox(
            server_mgmt_frame, values=[], state="readonly", width=200,
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_saved_servers_combobox.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="ew")
        self.rcon_saved_servers_combobox.configure(command=self.rcon_fill_server_credentials)

        self.rcon_save_button = ctk.CTkButton(
            server_mgmt_frame, text="Save Server", command=self.rcon_save_server_credentials,
            width=100, font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_save_button.grid(row=0, column=1, padx=(0, 5), pady=0)

        self.rcon_delete_button = ctk.CTkButton(
            server_mgmt_frame, text="Delete Server", command=self.rcon_delete_server,
            fg_color=COLOR_DANGER, hover_color="#ff006e", width=100,
            font=ctk.CTkFont(size=12), corner_radius=8
        )
        self.rcon_delete_button.grid(row=0, column=2, padx=0, pady=0)

        self.load_rcon_saved_servers()

    # Core Logic
    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Config error: {e}")
                self.show_error("Config Error", "Configuration file is corrupt or unreadable. Using default settings.")
        return {"profiles": {"Default": {"mod_folder": "", "game_exe": ""}}, "active_profile": "Default"}

    def save_config(self):
        self.config = {
            "geometry": self.geometry(),
            "profiles": self.profiles,
            "active_profile": self.active_profile,
            "appearance_mode": "Dark"
        }
        if self.active_profile and self.active_profile in self.profiles:
            self.profiles[self.active_profile].update({
                "devmode": self.devmode_var.get(),
                "logfile": self.logfile_var.get(),
                "custom_params": self.custom_params_var.get()
            })
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save config: {e}")

    def on_close(self):
        if self.game_process and self.game_process.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(self.game_process.pid)])
                else:
                    self.game_process.terminate()
                    try:
                        self.game_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.game_process.kill()
            except Exception as e:
                logging.error(f"Failed to terminate game process: {e}")
        self.save_config()
        self.destroy()

    def refresh_profile_dropdown(self):
        names = list(self.profiles.keys())
        if not names:
            names = ["Default"]
            self.profiles["Default"] = {"mod_folder": "", "game_exe": ""}
            self.active_profile = "Default"
        self.opt_profile.configure(values=names)
        if self.active_profile in names:
            self.opt_profile.set(self.active_profile)
        else:
            self.opt_profile.set(names[0])
            self.change_profile_event(names[0])

    def change_profile_event(self, new_profile: str):
        self.active_profile = new_profile
        self.load_profile_folder()
        self.update_status()
        self.save_config()

    def create_profile(self):
        name = self.ask_string("New Profile", "Enter profile name:")
        if not name:
            return
        if name in self.profiles:
            self.show_error("Error", "Profile exists.")
            return
        self.profiles[name] = {
            "mod_folder": "",
            "game_exe": "",
            "devmode": False,
            "logfile": False,
            "custom_params": ""
        }
        self.active_profile = name
        self.refresh_profile_dropdown()
        self.load_profile_folder()
        self.show_info("Profile Created", f"Profile '{name}' created.\nPlease select a Base Folder.")

    def rename_profile(self):
        if not self.active_profile or not self.profiles:
            self.show_error("Error", "No active profile to rename.")
            return
        new_name = self.ask_string("Rename", "New name:", initialvalue=self.active_profile)
        if not new_name or new_name == self.active_profile:
            return
        if new_name in self.profiles:
            self.show_error("Error", "Profile name already exists.")
            return
        data = self.profiles.pop(self.active_profile)
        self.profiles[new_name] = data
        self.active_profile = new_name
        self.refresh_profile_dropdown()
        self.save_config()
        self.update_status()

    def delete_profile(self):
        if not self.active_profile:
            self.show_error("Error", "No profile selected to delete.")
            return
        is_last_profile = len(self.profiles) == 1
        if is_last_profile:
            if not self.ask_yesno("Delete Last Profile", f"Profile '{self.active_profile}' is the only profile. Deleting it will create a new 'Default' profile. Proceed?"):
                return
        else:
            if not self.ask_yesno("Delete Profile", f"Permanently delete profile '{self.active_profile}'?"):
                return
        del self.profiles[self.active_profile]
        if self.profiles:
            self.active_profile = next(iter(self.profiles))
        else:
            self.active_profile = "Default"
            self.profiles["Default"] = {"mod_folder": "", "game_exe": ""}
        self.refresh_profile_dropdown()
        self.load_profile_folder()
        self.save_config()
        self.show_info("Profile Deleted", "Profile successfully deleted.")

    def load_profile_folder(self):
        if not self.active_profile:
            return
        profile = self.profiles[self.active_profile]
        folder_str = profile.get("mod_folder", "")
        self.game_exe_path = Path(profile.get("game_exe", "")) if profile.get("game_exe") else None
        self.devmode_var.set(profile.get("devmode", False))
        self.logfile_var.set(profile.get("logfile", False))
        self.custom_params_var.set(profile.get("custom_params", ""))
        if folder_str and os.path.exists(folder_str):
            self.set_mod_folder(Path(folder_str))
        else:
            self.path_var.set("Base folder path missing or invalid for this profile.")
            self.mod_folder = None
            self.refresh_list()

    def browse_folder(self):
        default_path = Path.home()
        if os.name == 'nt':
            default_path = Path("C:/")
        elif os.name == 'posix':
            default_path = Path.home() / "Games"
        path_str = filedialog.askdirectory(parent=self, title="Select JK2/Base Folder", initialdir=str(default_path))
        if path_str:
            path_obj = Path(path_str)
            self.set_mod_folder(path_obj)
            if self.active_profile:
                self.profiles[self.active_profile]["mod_folder"] = path_str
                self.save_config()
                self.update_status()

    def set_mod_folder(self, path_obj: Path):
        self.mod_folder = path_obj
        self.path_var.set(str(path_obj))
        disabled = self.mod_folder / DISABLED_DIR_NAME
        if not disabled.exists():
            try:
                disabled.mkdir()
            except Exception as e:
                logging.error(f"Failed to create disabled directory: {e}")
        self.refresh_list()

    def open_in_explorer(self):
        if not self.mod_folder:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", str(self.mod_folder)])
            elif os.uname().sysname == "Darwin":
                subprocess.Popen(["open", str(self.mod_folder)])
            else:
                subprocess.Popen(["xdg-open", str(self.mod_folder)])
        except Exception as e:
            self.show_error("Error", f"Could not open folder: {e}")

    def _clear_treeview(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def _collect_mods(self) -> list[dict]:
        mods = []
        search = self.search_var.get().lower()

        def collect(base: Path, enabled: bool):
            if not base.exists():
                return
            try:
                for f in base.iterdir():
                    if not f.is_file():
                        continue
                    if f.name in PROTECTED_ASSETS:
                        continue
                    if f.suffix.lower() != ".pk3":
                        continue
                    if search and search not in f.name.lower():
                        continue
                    size_mb = f.stat().st_size / (1024 * 1024)
                    mods.append({
                        "path": f,
                        "enabled": enabled,
                        "size": f"{size_mb:.2f} MB",
                        "sort_key": f.name.lower()
                    })
            except Exception as e:
                logging.error(f"Error collecting mods: {e}")

        if self.mod_folder:
            collect(self.mod_folder, True)
            collect(self.mod_folder / DISABLED_DIR_NAME, False)

        mods.sort(key=lambda m: m["sort_key"])
        return mods

    def _populate_treeview(self, mods: list[dict]):
        self.mod_index.clear()
        for i, mod in enumerate(mods):
            iid = f"mod_{i}"
            self.mod_index[iid] = mod["path"]
            status_text = "ENABLED" if mod["enabled"] else "DISABLED"
            tag = "enabled" if mod["enabled"] else "disabled"
            self.tree.insert("", "end", iid=iid, values=(mod["size"], status_text, mod["path"].name), tags=(tag,))

    def refresh_list(self):
        self._clear_treeview()
        mods = self._collect_mods()
        self._populate_treeview(mods)
        self.auto_adjust_columns()
        self.update_status()

    def auto_adjust_columns(self):
        if not self.tree.get_children():
            self.tree.column("size", width=100, stretch=tk.NO)
            self.tree.column("status", width=100, stretch=tk.NO)
            self.tree.column("priority", width=400, stretch=tk.YES)
            return
        PIXEL_PER_CHAR = 10
        padding = 20
        widths = {
            "size": len(self.tree.heading("size", option="text")) * PIXEL_PER_CHAR,
            "status": len(self.tree.heading("status", option="text")) * PIXEL_PER_CHAR,
        }
        for iid in self.tree.get_children():
            values = self.tree.item(iid, 'values')
            if len(values) >= 3:
                widths["size"] = max(widths["size"], len(values[0]) * PIXEL_PER_CHAR)
                widths["status"] = max(widths["status"], len(values[1]) * PIXEL_PER_CHAR)
        self.tree.column("size", width=max(100, widths["size"] + padding), stretch=tk.NO)
        self.tree.column("status", width=max(100, widths["status"] + padding), stretch=tk.NO)
        self.tree.column("priority", minwidth=400, width=400, stretch=tk.YES)

    def update_status(self):
        if not self.active_profile:
            self.status_var.set("No profile selected.")
            return
        enabled_count = sum(1 for p in self.mod_index.values() if p.parent == self.mod_folder)
        disabled_count = sum(1 for p in self.mod_index.values() if p.parent == (self.mod_folder / DISABLED_DIR_NAME))
        self.status_var.set(f"Profile: {self.active_profile} | Enabled: {enabled_count} | Disabled: {disabled_count} | Total: {enabled_count + disabled_count}")

    def toggle_mod_action(self, path: Path, target_state: str | None = None) -> bool:
        if not self.mod_folder:
            return False
        is_enabled = path.parent == self.mod_folder
        if target_state == "enable" and is_enabled:
            return False
        if target_state == "disable" and not is_enabled:
            return False
        target_dir = self.mod_folder if not is_enabled else self.mod_folder / DISABLED_DIR_NAME
        dest = target_dir / path.name
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if os.name != 'nt':
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
            path.rename(dest)
            return True
        except Exception as e:
            self.show_error("Toggle Error", f"Failed to move {path.name}: {e}")
            return False

    def toggle_selected_mods_and_status(self, force: str | None = None):
        if not self.tree.selection():
            return
        success = True
        for iid in self.tree.selection():
            if iid in self.mod_index:
                if not self.toggle_mod_action(self.mod_index[iid], force):
                    success = False
        self.refresh_list()
        if not success:
            self.show_error("Error", "Some mods failed to toggle.")

    def install_mods_threaded(self):
        if not self.mod_folder:
            return self.show_error("Error", "Select Base Folder first.")
        files = self.ask_open_files(title="Select PK3 Files", filetypes=[("PK3", "*.pk3")])
        if not files:
            return
        self.set_processing_state(True)
        threading.Thread(target=self._install_worker, args=(files,), daemon=True).start()

    def _install_worker(self, files: list[str]):
        count, errors = 0, 0
        target_dir = self.mod_folder
        for i, f_path_str in enumerate(files):
            self.after(0, lambda: self.status_var.set(f"Installing... ({i+1}/{len(files)})"))
            f = Path(f_path_str)
            try:
                if (target_dir / f.name).exists():
                    if not self.ask_yesno("Overwrite?", f"'{f.name}' already exists. Overwrite?"):
                        continue
                shutil.copy2(f, target_dir / f.name)
                count += 1
            except Exception as e:
                logging.error(f"Failed to install {f.name}: {e}")
                errors += 1
        self.after(0, lambda: self._op_complete(f"Installed {count} mods ({errors} errors)."))

    def delete_selected_threaded(self):
        items = self.tree.selection()
        if not items:
            return
        valid_items = [iid for iid in items if iid in self.mod_index]
        if not valid_items:
            return
        if not self.ask_yesno("Delete", f"Permanently delete {len(valid_items)} file(s)?"):
            return
        self.set_processing_state(True)
        threading.Thread(target=self._delete_worker, args=(valid_items,), daemon=True).start()

    def _delete_worker(self, items: list[str]):
        count = 0
        for iid in items:
            try:
                self.mod_index[iid].unlink()
                count += 1
            except Exception as e:
                logging.error(f"Failed to delete {self.mod_index[iid].name}: {e}")
        self.after(0, lambda: self._op_complete(f"Deleted {count} files."))

    def start_game_threaded(self):
        if not self.game_exe_path or not Path(self.game_exe_path).exists():
            self.show_info("Select Executable", "Please locate the game executable, for example 'jk2mvmp(.exe)' or 'nwhmp(.exe)'.")
            exe = filedialog.askopenfilename(parent=self, title="Select Game Executable")
            if not exe:
                return
            self.game_exe_path = Path(exe)
            if self.active_profile:
                self.profiles[self.active_profile]["game_exe"] = str(exe)
            self.save_config()
        self.set_processing_state(True)
        threading.Thread(target=self._launch_game, daemon=True).start()

    def _launch_game(self):
        try:
            exe_path = Path(self.game_exe_path)
            if os.name != 'nt':
                exe_path.chmod(exe_path.stat().st_mode | stat.S_IEXEC)
            params = []
            if self.devmode_var.get():
                params.append("+developer 1")
            if self.logfile_var.get():
                params.append("+logfile 2")
            custom = self.custom_params_var.get().strip()
            if custom:
                params.extend(custom.split())
            command = [str(exe_path)] + params
            self.game_process = subprocess.Popen(command, cwd=str(exe_path.parent))
            self.after(0, lambda: self._op_complete("Game launched successfully."))
        except Exception as e:
            error_msg = f"Failed to launch game: {e}"
            self.after(0, lambda: self.show_error("Launch Error", error_msg))
            self.after(0, lambda: self.set_processing_state(False))

    def _op_complete(self, msg: str):
        self.set_processing_state(False)
        self.status_var.set(msg)
        self.refresh_list()

    def set_processing_state(self, is_processing: bool):
        state = "disabled" if is_processing else "normal"
        self.btn_launch.configure(state=state)
        self.btn_install.configure(state=state)
        self.btn_enable.configure(state=state)
        self.btn_disable.configure(state=state)
        self.btn_delete_mod.configure(state=state)
        self.btn_refresh_downloads.configure(state=state)
        self.btn_download_selected.configure(state=state)

    def rename_mod_dialog(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid not in self.mod_index:
            return
        path = self.mod_index[iid]
        new_name = self.ask_string("Rename", "New filename:", initialvalue=path.name)
        if not new_name:
            return
        if not new_name.lower().endswith(".pk3"):
            new_name += ".pk3"
        if not re.match(r'^[a-zA-Z0-9_\-\.]+\.pk3$', new_name):
            self.show_error("Error", "Invalid filename.")
            return
        try:
            path.rename(path.parent / new_name)
            self.refresh_list()
        except Exception as e:
            self.show_error("Error", str(e))

    def export_json(self):
        if not self.mod_folder:
            return
        filename = self.ask_save_file(title="Export JSON", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not filename:
            return
        mod_list = []
        self._load_order_counter = 1

        def collect_dir_data(base: Path, status: str):
            if not base.exists():
                return
            sorted_files = sorted(base.iterdir(), key=lambda p: p.name.lower())
            for fpath in sorted_files:
                if fpath.suffix.lower() == ".pk3":
                    if fpath.name in PROTECTED_ASSETS:
                        continue
                    file_stats = fpath.stat()
                    size_bytes = file_stats.st_size
                    size_mb = size_bytes / (1024 * 1024)
                    file_hash = get_sha256_hash(fpath)
                    raw_timestamp = file_stats.st_mtime
                    formatted_time = datetime.datetime.fromtimestamp(raw_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    current_order = self._load_order_counter
                    self._load_order_counter += 1
                    mod_list.append({
                        "name": fpath.name,
                        "status": status,
                        "load_order": current_order,
                        "size_mb": size_mb,
                        "path": str(fpath),
                        "sha256": file_hash,
                        "last_modified": formatted_time
                    })

        collect_dir_data(self.mod_folder, "Enabled")
        collect_dir_data(self.mod_folder / DISABLED_DIR_NAME, "Disabled")
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(mod_list, f, indent=4)
            self.show_info("Exported", f"List saved to {filename}")
        except Exception as e:
            self.show_error("Export Error", f"Failed to save JSON: {e}")

    def fetch_mod_list(self):
        try:
            encoded_parts = [
                "aHR0cHM6Ly9qazJ0",
                "LmRkbnMubmV0L21v",
                "ZG1hbmFnZXIvbW9k",
                "cy5qc29u"
            ]
            encoded_url = "".join(encoded_parts)
            api_url = base64.b64decode(encoded_url).decode("utf-8")

            if not api_url:
                raise ValueError("API URL is not set.")

            response = requests.get(api_url, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.show_error("Download Error", f"Failed to fetch mod list: {e}")
            return []

    def refresh_download_list(self):
        mods = self.fetch_mod_list()
        search_term = self.download_search_var.get().lower()

        if search_term:
            mods = [
                mod for mod in mods
                if (
                    search_term in mod["name"].lower()
                    or search_term in mod.get("author", "").lower()
                    or search_term in mod.get("uploader", "").lower()
                    or search_term in mod.get("category", "").lower()
                )
            ]

        self._clear_download_treeview()
        self._populate_download_treeview(mods)

    def _clear_download_treeview(self):
        for i in self.download_tree.get_children():
            self.download_tree.delete(i)

    def _populate_download_treeview(self, mods):
        for mod in mods:
            iid = mod["download_url"]
            self.download_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    mod["name"],
                    mod.get("author", "Unknown"),
                    mod["size"],
                    mod.get("category", "N/A"),
                    mod.get("uploader", "Unknown"),
                    mod.get("date", "N/A"),
                    "✓" if "preview_image" in mod else "✗"
                ),
                tags=("centered",)
            )

        self.download_tree.tag_configure("centered", anchor="center")

    def download_selected_mods(self):
        selected = self.download_tree.selection()
        if not selected:
            return self.show_error("Error", "No mod selected.")

        if not self.mod_folder:
            return self.show_error("Error", "Select Base Folder first.")

        for iid in selected:
            mod_url = iid
            mod_name = self.download_tree.item(iid, "values")[0]
            self.set_processing_state(True)
            self.status_var.set(f"Downloading {mod_name}...")
            threading.Thread(
                target=self._download_mod_worker,
                args=(mod_url, mod_name),
                daemon=True
            ).start()

    def _download_mod_worker(self, mod_url, mod_name):
        try:
            response = requests.get(mod_url, stream=True, timeout=10)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            save_path = self.mod_folder / f"{mod_name}.pk3"
            downloaded = 0
            with open(save_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress = downloaded / total_size if total_size > 0 else 0
                    percent = int(progress * 100)
                    self.after(0, lambda: self.download_progress.set(progress))
                    self.after(0, lambda: self.download_progress_percent.configure(text=f"{percent}%"))
            self.after(0, lambda: self._op_complete(f"Downloaded {mod_name} successfully!"))
        except Exception as e:
            self.after(0, lambda: self.show_error("Download Error", f"Failed to download {mod_name}: {e}"))
            self.after(0, lambda: self.set_processing_state(False))
        finally:
            self.after(0, lambda: self.download_progress.set(0))
            self.after(0, lambda: self.download_progress_percent.configure(text="0%"))

    def on_download_mod_selected(self, event):
        selected = self.download_tree.selection()
        if not selected:
            return
        iid = selected[0]
        mod_url = iid
        mod_name = self.download_tree.item(iid, "values")[0]
        preview_url = None
        for mod in self.fetch_mod_list():
            if mod["download_url"] == mod_url:
                preview_url = mod.get("preview_image")
                break
        if preview_url:
            self._load_preview_image(preview_url)
        else:
            self.download_preview_canvas.configure(image=None, text="No Preview")

    def _load_preview_image(self, preview_url):
        try:
            response = requests.get(preview_url, timeout=5)
            response.raise_for_status()
            img_data = response.content
            img = Image.open(io.BytesIO(img_data))
            preview_width = 200
            ratio = preview_width / float(img.size[0])
            preview_height = int((float(img.size[1]) * float(ratio)))
            img = img.resize((preview_width, preview_height), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(preview_width, preview_height))
            self.download_preview_canvas.configure(image=ctk_img, text="")
            self.download_preview_canvas.image = ctk_img
        except Exception as e:
            self.download_preview_canvas.configure(image=None, text="Preview Error")

    # RCON Logic
    def load_rcon_saved_servers(self):
        self.rcon_config.read(RCON_CONFIG_FILE)
        saved_servers = self.rcon_config.sections()
        self.rcon_saved_servers_combobox.configure(values=saved_servers)

    def rcon_fill_server_credentials(self, choice: str):
        server_name = choice
        if server_name:
            self.rcon_config.read(RCON_CONFIG_FILE)
            self.rcon_server_name_entry.delete(0, tk.END)
            self.rcon_server_name_entry.insert(0, server_name)
            self.rcon_server_ip_entry.delete(0, tk.END)
            self.rcon_server_ip_entry.insert(0, self.rcon_config[server_name]['ip'])
            self.rcon_server_port_entry.delete(0, tk.END)
            self.rcon_server_port_entry.insert(0, self.rcon_config[server_name]['port'])
            self.rcon_password_entry.delete(0, tk.END)
            self.rcon_password_entry.insert(0, self.rcon_config[server_name]['password'])

    def rcon_delete_server(self):
        server_name = self.rcon_saved_servers_combobox.get()
        if not server_name:
            self.show_error("Error", "No server selected to delete.")
            return
        if not self.ask_yesno("Delete Server", f"Permanently delete server '{server_name}'?"):
            return
        try:
            self.rcon_config.read(RCON_CONFIG_FILE)
            if server_name in self.rcon_config:
                self.rcon_config.remove_section(server_name)
                with open(RCON_CONFIG_FILE, 'w') as configfile:
                    self.rcon_config.write(configfile)
                self.load_rcon_saved_servers()
                self.show_info("Server Deleted", f"Server '{server_name}' successfully deleted.")
        except Exception as e:
            self.show_error("Error", f"Failed to delete server: {e}")

    def rcon_save_server_credentials(self):
        server_name = self.rcon_server_name_entry.get()
        server_ip = self.rcon_server_ip_entry.get()
        server_port = self.rcon_server_port_entry.get()
        rcon_password = self.rcon_password_entry.get()
        if not server_name or not server_ip or not server_port:
            self.show_error("Error", "Server name, IP, and port are required.")
            return
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', server_name):
            self.show_error("Error", "Invalid server name.")
            return
        self.rcon_config.read(RCON_CONFIG_FILE)
        self.rcon_config[server_name] = {
            'ip': server_ip,
            'port': server_port,
            'password': rcon_password
        }
        with open(RCON_CONFIG_FILE, 'w') as configfile:
            self.rcon_config.write(configfile)
        self.load_rcon_saved_servers()
        self.show_info("Server Saved", f"Server '{server_name}' successfully saved.")

    def rcon_send_on_enter(self, event):
        self.rcon_send_command()

    def rcon_send_command(self):
        server_ip = self.rcon_server_ip_entry.get()
        server_port = self.rcon_server_port_entry.get()
        rcon_password = self.rcon_password_entry.get()
        command = self.rcon_input_entry.get()
        if not server_ip or not server_port or not command:
            self.show_error("Error", "Server IP, port, and command are required.")
            return
        if not re.match(r'^[a-zA-Z0-9_\-\.\s]+$', command):
            self.show_error("Error", "Invalid command.")
            return
        try:
            server_port = int(server_port)
            self.socket.sendto(
                b"\xff\xff\xff\xffrcon %s %s\n" % (rcon_password.encode(), command.encode()),
                (server_ip, server_port)
            )
            response, _ = self.socket.recvfrom(4096)
            response = response.decode('utf-8', 'ignore')
            cleaned_response = clean_rcon_response(response)
            self.rcon_output_text.insert("end", f">>> {command}\n{cleaned_response}\n\n")
            self.rcon_output_text.see("end")
        except socket.timeout:
            self.rcon_output_text.insert("end", f"Connection timed out.\n\n")
            self.rcon_output_text.see("end")
        except ValueError:
            self.show_error("Error", "Server port must be a valid number.")
        except Exception as e:
            self.rcon_output_text.insert("end", f"Error: {str(e)}\n\n")
            self.rcon_output_text.see("end")
        finally:
            self.rcon_input_entry.delete(0, tk.END)

    # UI Helpers
    def on_mod_selected(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        mod_name = selection[0]
        mod_path = self.mod_index.get(mod_name)
        if mod_path and mod_path.suffix.lower() == ".pk3":
            self.update_preview(mod_path)
        else:
            self.preview_canvas.configure(image=None, text="No Preview Available")

    def update_preview(self, pk3_path: Path):
        try:
            with zipfile.ZipFile(pk3_path, 'r') as z:
                img_exts = {'.jpg', '.jpeg', '.png', '.tga'}
                image_files = [f for f in z.namelist() if any(f.lower().endswith(ext) for ext in img_exts)]
                if not image_files:
                    self.preview_canvas.configure(image=None, text="No Image Found")
                    return
                best_match = image_files[0]
                for f in image_files:
                    if "levelshot" in f.lower() or "preview" in f.lower():
                        best_match = f
                        break
                with z.open(best_match) as img_file:
                    img_data = img_file.read()
                    try:
                        img = Image.open(io.BytesIO(img_data))
                        preview_width = self.preview_box.winfo_width() - 20
                        ratio = preview_width / float(img.size[0])
                        preview_height = int((float(img.size[1]) * float(ratio)))
                        img = img.resize((preview_width, preview_height), Image.Resampling.LANCZOS)
                        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(preview_width, preview_height))
                        self.preview_canvas.configure(image=ctk_img, text="")
                        self.preview_canvas.image = ctk_img
                    except Exception as e:
                        self.preview_canvas.configure(image=None, text="Unsupported Image")
        except Exception as e:
            self.preview_canvas.configure(image=None, text="Preview Error")

    def show_info(self, title: str, message: str):
        CTkMessagebox(title=title, message=message, icon="info", option_focus=1)

    def show_error(self, title: str, message: str):
        CTkMessagebox(title=title, message=message, icon="cancel")

    def ask_yesno(self, title: str, message: str) -> bool:
        msg = CTkMessagebox(title=title, message=message, icon="question", option_1="No", option_2="Yes")
        response = msg.get()
        return response == "Yes"

    def ask_string(self, title: str, prompt: str, initialvalue: str = "") -> str | None:
        return ctk_ask_string(self, title, prompt, initialvalue)

    def ask_open_files(self, title: str, filetypes: list[tuple[str, str]]) -> list[str]:
        return list(filedialog.askopenfilenames(parent=self, title=title, filetypes=filetypes))

    def ask_save_file(self, title: str, defaultextension: str, filetypes: list[tuple[str, str]]) -> str | None:
        return filedialog.asksaveasfilename(parent=self, title=title, defaultextension=defaultextension, filetypes=filetypes)

    def create_context_menu(self):
        bg_color = "#16213e"
        fg_color = "#ffffff"
        select_bg = COLOR_PRIMARY
        select_fg = "#ffffff"
        self.context_menu = tk.Menu(
            self, tearoff=0, bg=bg_color, fg=fg_color, activebackground=select_bg,
            activeforeground=select_fg, selectcolor=select_fg, relief="flat", borderwidth=0
        )
        self.context_menu.add_command(label="Toggle State", command=self.toggle_selected_mods_and_status)
        self.context_menu.add_command(label="Rename File", command=self.rename_mod_dialog)
        self.context_menu.add_separator(background=bg_color)
        self.context_menu.add_command(label="Delete File", command=self.delete_selected_threaded)

    def show_context_menu(self, event):
        if hasattr(self, 'context_menu') and self.context_menu:
            self.context_menu.destroy()
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.create_context_menu()
            self.context_menu.post(event.x_root, event.y_root)

    def update_preview_style(self, mode: str):
        self.preview_box.configure(fg_color="#16213e")
        self.preview_canvas.configure(text_color=COLOR_TEXT_DIM)

    def update_treeview_style(self, mode: str):
        style = ttk.Style()
        style.theme_use("default")

        bg_color = "#1a1a2e"
        fg_color = "#ffffff"
        field_bg = "#1a1a2e"
        header_bg = "#16213e"
        header_fg = "#ffffff"
        select_bg = COLOR_PRIMARY
        grid_line_color = "#3a86ff"
        scroll_trough = COLOR_SCROLL_TROUGH
        scroll_thumb = COLOR_SCROLL_THUMB
        scroll_arrow = COLOR_SCROLL_ARROW

        style.configure(
            "Treeview", background=bg_color, foreground=fg_color, fieldbackground=field_bg, borderwidth=0,
            font=("Roboto", 11), rowheight=28, fieldrelief="solid", bordercolor=grid_line_color
        )
        style.configure(
            "Treeview.Heading", background=header_bg, foreground=header_fg, relief="flat",
            font=("Roboto", 11, "bold"), separator=True
        )
        style.map(
            "Treeview.Heading", background=[("!active", header_bg), ("active", header_bg)],
            foreground=[("!active", header_fg), ("active", header_fg)], relief=[("active", "flat")]
        )
        style.map(
            "Treeview", background=[("selected", select_bg)], fieldbackground=[("focus", field_bg), ("!focus", field_bg)]
        )
        style.layout("Treeview", [('Treeview.treearea', {'sticky': 'nswe'})])

        style.configure(
            "Custom.Vertical.TScrollbar", troughcolor=scroll_trough, background=scroll_thumb,
            fieldbackground=scroll_thumb, fieldrelief="flat", bordercolor=scroll_trough,
            arrowcolor=scroll_arrow, troughrelief="flat", relief="flat", arrowsize=16
        )
        style.map(
            "Custom.Vertical.TScrollbar",
            background=[("active", scroll_thumb)],
            troughcolor=[("active", scroll_trough)],
            bordercolor=[("active", scroll_trough)]
        )

        self.tree.tag_configure("enabled", foreground=COLOR_SUCCESS)
        self.tree.tag_configure("disabled", foreground=COLOR_DANGER)

    def check_for_updates_threaded(self):
        self.btn_check_updates.configure(state="disabled")
        threading.Thread(target=self.check_for_updates, daemon=True).start()

    def check_for_updates(self):
        try:
            response = requests.get(UPDATE_VERSION_URL, timeout=5)
            response.raise_for_status()
            latest_version = response.text.strip()
            if self.version_tuple(latest_version) <= self.version_tuple(APP_VERSION):
                self.after(0, lambda: self.show_info("Up to Date", f"You are running the latest version ({APP_VERSION})."))
            else:
                self.after(0, lambda: self.show_info(
                    "Update Available",
                    f"A new version is available!\n\nCurrent: {APP_VERSION}\nLatest: {latest_version}\n\nCheck the GitHub Repository."
                ))
        except Exception as e:
            self.after(0, lambda: self.show_error("Update Check Failed", f"Could not check for updates.\n\n{e}"))
        finally:
            self.after(0, lambda: self.btn_check_updates.configure(state="normal"))

    def version_tuple(self, v: str) -> tuple[int, int, int]:
        try:
            return tuple(map(int, v.split(".")))
        except ValueError:
            return (0, 0, 0)

if __name__ == "__main__":
    scaling = get_dpi_scaling()
    ctk.set_widget_scaling(scaling)
    ctk.set_window_scaling(scaling)
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("dark-blue")

    app = JK2ModManager()
    app.refresh_download_list()
    app.mainloop()
