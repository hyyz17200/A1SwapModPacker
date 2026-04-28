from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_INSERT_BEFORE_MARKER = ";=====printer finish  sound========="

MetadataMode = Literal["source", "sum"]
LineEnding = Literal["lf", "crlf"]


@dataclass(frozen=True)
class PlateJob:
    source_3mf: Path
    copies: int = 1


@dataclass(frozen=True)
class GcodePatchRule:
    name: str
    find: str
    replace: str
    flag: str = ""
    enabled: bool = True
    max_count: int = 1


@dataclass(frozen=True)
class GcodePatchConfig:
    rules: tuple[GcodePatchRule, ...]
    insert_before_marker: str = DEFAULT_INSERT_BEFORE_MARKER


@dataclass(frozen=True)
class BuildOptions:
    swap_gcode: Path | str
    output_3mf: Path
    cool_bed_temp: int | None = 45
    wait_after_eject_seconds: int = 45
    show_plate_number: bool = True
    swap_after_final: bool = True
    metadata_mode: MetadataMode = "source"
    line_ending: LineEnding = "crlf"
    add_preview_label: bool = True
    apply_gcode_patches: bool = True
    swap_gcode_dir: Path | None = None


@dataclass
class PlateSource:
    source_3mf: Path
    member_name: str
    gcode_text: str
    prediction_seconds: float | None = None
    weight_grams: float | None = None
    filament_used_m: float | None = None
    filament_used_g: float | None = None


@dataclass(frozen=True)
class ThreeMfSummary:
    source_3mf: Path
    plate_count: int
    prediction_seconds: float | None = None
    weight_grams: float | None = None
    filament_used_m: float | None = None
    filament_used_g: float | None = None


@dataclass(frozen=True)
class BuildSummary:
    plate_count: int
    total_prediction_seconds: float | None
    total_weight_grams: float | None
    total_filament_used_m: float | None
    total_filament_used_g: float | None


@dataclass
class BuildResult:
    output_3mf: Path
    plate_count: int
    total_prediction_seconds: float | None
    total_weight_grams: float | None
    gcode_md5: str
