from __future__ import annotations

import sys
from pathlib import Path

APP_DIR_NAME = "a1_swap_mod_packer"
SWAP_GCODE_DIR_NAME = "swap_gcode"
PATCH_CONFIG_FILE_NAME = "gcode_patches.ini"


def program_root() -> Path:
    compiled = globals().get("__compiled__")
    if compiled is not None and getattr(compiled, "onefile", False):
        containing_dir = getattr(compiled, "containing_dir", None)
        if containing_dir:
            return Path(containing_dir).resolve()

        original_argv0 = getattr(compiled, "original_argv0", None)
        if original_argv0:
            return Path(original_argv0).resolve().parent

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_swap_gcode_dir() -> Path:
    return program_root() / SWAP_GCODE_DIR_NAME


def default_patch_config_path() -> Path:
    return program_root() / PATCH_CONFIG_FILE_NAME


def user_settings_path() -> Path:
    return program_root() / "settings.json"
