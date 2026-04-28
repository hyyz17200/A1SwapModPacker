from __future__ import annotations

import configparser
from pathlib import Path

from .gcode import normalize_newlines
from .models import DEFAULT_INSERT_BEFORE_MARKER, GcodePatchConfig, GcodePatchRule
from .paths import default_patch_config_path


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def parse_patch_config(path: Path | None = None) -> GcodePatchConfig:
    config_path = path or default_patch_config_path()
    if not config_path.exists():
        return GcodePatchConfig(rules=())
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(config_path, encoding="utf-8-sig")

    insert_before_marker = DEFAULT_INSERT_BEFORE_MARKER
    if parser.has_section("swap"):
        insert_before_marker = parser.get("swap", "insert_before_marker", fallback=insert_before_marker).strip().strip('"')

    rules: list[GcodePatchRule] = []
    for section in parser.sections():
        if section.lower().startswith("patch."):
            enabled = parse_bool(parser.get(section, "enabled", fallback="true"), True)
            find = parser.get(section, "find", fallback="").strip().strip('"')
            replace = parser.get(section, "replace", fallback="").strip().strip('"')
            flag = parser.get(section, "flag", fallback="").strip().strip('"')
            max_count_text = parser.get(section, "max_count", fallback="1").strip()
            try:
                max_count = max(0, int(max_count_text))
            except ValueError:
                max_count = 1
            if find and enabled:
                rules.append(GcodePatchRule(section.split(".", 1)[1], find, replace, flag, enabled, max_count))

    if parser.has_section("Gcode"):
        gcode = parser["Gcode"]
        base_names = sorted(
            key[:-5]
            for key in gcode.keys()
            if key.startswith("Postion") and key.endswith("_flag")
        )
        for base in base_names:
            find = gcode.get(base, "").strip().strip('"')
            replace = gcode.get(f"{base}_edit", "").strip().strip('"')
            flag = gcode.get(f"{base}_flag", "").strip().strip('"')
            if find and replace:
                rules.append(GcodePatchRule(base, find, replace, flag, True, 1))
        marker = gcode.get("FinishFlag", "").strip().strip('"')
        if marker:
            insert_before_marker = marker
    return GcodePatchConfig(tuple(rules), insert_before_marker)


def apply_patch_rule(text: str, rule: GcodePatchRule) -> str:
    if not rule.enabled or not rule.find or rule.max_count == 0:
        return text
    lines = normalize_newlines(text).split("\n")
    start_index = 0
    if rule.flag:
        for index, line in enumerate(lines):
            if line.strip() == rule.flag or rule.flag in line:
                start_index = index + 1
                break
    replacements = 0
    for index in range(start_index, len(lines)):
        if lines[index].strip() == rule.find or lines[index] == rule.find:
            lines[index] = rule.replace
            replacements += 1
            if replacements >= rule.max_count:
                break
    return "\n".join(lines)


def apply_gcode_patches(text: str, config: GcodePatchConfig) -> str:
    patched = normalize_newlines(text)
    for rule in config.rules:
        patched = apply_patch_rule(patched, rule)
    return patched
