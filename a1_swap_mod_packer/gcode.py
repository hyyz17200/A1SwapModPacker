from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import DEFAULT_INSERT_BEFORE_MARKER, LineEnding
from .paths import default_swap_gcode_dir

M73_RE = re.compile(r"^(M73\s+P\d+\s+R)(\d+)(.*)$", re.MULTILINE)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def apply_line_ending(text: str, line_ending: LineEnding) -> bytes:
    normalized = normalize_newlines(text)
    if line_ending == "crlf":
        normalized = normalized.replace("\n", "\r\n")
    return normalized.encode("utf-8")


def read_swap_gcode_file(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def resolve_swap_gcode_path(value: Path | str, swap_gcode_dir: Path | None = None) -> Path:
    raw = Path(value)
    if raw.exists():
        return raw
    search_dir = swap_gcode_dir or default_swap_gcode_dir()
    candidate = search_dir / str(value)
    if candidate.exists():
        return candidate
    if raw.suffix:
        raise FileNotFoundError(f"Swap G-code file not found: {value}")
    for suffix in (".gcode", ".nc", ".ngc", ".txt"):
        candidate = search_dir / f"{value}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Swap G-code file not found: {value}")


def list_swap_gcode_files(directory: Path | None = None) -> list[Path]:
    root = directory or default_swap_gcode_dir()
    if not root.exists():
        return []
    allowed = {".gcode", ".nc", ".ngc", ".txt"}
    return sorted(path for path in root.iterdir() if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in allowed)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "Unknown"
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_filament(weight_grams: float | None, used_m: float | None = None) -> str:
    if weight_grams is None and used_m is None:
        return "Unknown"
    parts: list[str] = []
    if weight_grams is not None:
        parts.append(f"{weight_grams:.2f} g")
    if used_m is not None:
        parts.append(f"{used_m:.2f} m")
    return " / ".join(parts)


@dataclass(frozen=True)
class M73OffsetTemplate:
    segments: tuple[str, ...]
    base_remaining_minutes: tuple[int, ...]

    def apply(self, plate_number: int) -> str:
        if not self.base_remaining_minutes:
            return self.segments[0]
        minute_offset = plate_number * 100 * 60
        parts: list[str] = []
        for index, remaining_minutes in enumerate(self.base_remaining_minutes):
            parts.append(self.segments[index])
            parts.append(str(remaining_minutes + minute_offset))
        parts.append(self.segments[-1])
        return "".join(parts)


def prepare_m73_offset_template(gcode: str) -> M73OffsetTemplate:
    segments: list[str] = []
    base_remaining_minutes: list[int] = []
    last_index = 0
    for match in M73_RE.finditer(gcode):
        segments.append(gcode[last_index : match.start(2)])
        base_remaining_minutes.append(int(match.group(2)))
        last_index = match.end(2)
    segments.append(gcode[last_index:])
    return M73OffsetTemplate(tuple(segments), tuple(base_remaining_minutes))


def apply_plate_number_offset(gcode: str, plate_number: int) -> str:
    return prepare_m73_offset_template(gcode).apply(plate_number)


def build_swap_block(swap_gcode: str, cool_bed_temp: int | None, wait_seconds: int) -> str:
    parts: list[str] = []
    if cool_bed_temp is not None:
        parts.append(f"M190 S{int(cool_bed_temp)}")
    parts.append(normalize_newlines(swap_gcode).rstrip("\n"))
    if wait_seconds > 0:
        parts.append(f"G4 P{int(wait_seconds) * 1000}")
    return "\n".join(part for part in parts if part) + "\n"


def insert_swap_block(gcode: str, swap_block: str, insert_before_marker: str = DEFAULT_INSERT_BEFORE_MARKER) -> str:
    text = normalize_newlines(gcode)
    marker = insert_before_marker.strip()
    if marker:
        marker_index = text.find("\n" + marker)
        if marker_index == -1 and text.startswith(marker):
            marker_index = 0
        if marker_index != -1:
            prefix = text[: marker_index + (1 if marker_index > 0 else 0)]
            suffix = text[marker_index + (1 if marker_index > 0 else 0) :]
            return prefix + swap_block + suffix
    return text.rstrip("\n") + "\n" + swap_block
