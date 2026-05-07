from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from .archive import GCODE_MEMBER_RE, list_gcode_members
from .models import BuildOptions, BuildSummary, PlateJob, PlateSource, ThreeMfSummary


def read_slice_plate_metadata(archive: zipfile.ZipFile) -> dict[int, dict[str, str]]:
    try:
        data = archive.read("Metadata/slice_info.config")
    except KeyError:
        return {}
    root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    result: dict[int, dict[str, str]] = {}
    for plate in root.findall("plate"):
        plate_data: dict[str, str] = {}
        for metadata in plate.findall("metadata"):
            key = metadata.attrib.get("key")
            value = metadata.attrib.get("value")
            if key is not None and value is not None:
                plate_data[key] = value
        index_text = plate_data.get("index")
        try:
            index = int(index_text) if index_text else len(result) + 1
        except ValueError:
            index = len(result) + 1
        result[index] = plate_data
    return result


def read_filament_metadata(archive: zipfile.ZipFile) -> dict[int, dict[str, str]]:
    try:
        data = archive.read("Metadata/slice_info.config")
    except KeyError:
        return {}
    root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    result: dict[int, dict[str, str]] = {}
    for plate in root.findall("plate"):
        plate_index = 1
        for metadata in plate.findall("metadata"):
            if metadata.attrib.get("key") == "index":
                try:
                    plate_index = int(metadata.attrib.get("value", "1"))
                except ValueError:
                    plate_index = 1
                break
        filament = plate.find("filament")
        if filament is not None:
            result[plate_index] = dict(filament.attrib)
    return result


def read_model_settings_gcode_members(archive: zipfile.ZipFile) -> list[str]:
    try:
        data = archive.read("Metadata/model_settings.config")
    except KeyError:
        return []
    try:
        root = ET.fromstring(data.decode("utf-8-sig", errors="replace"))
    except Exception:
        return []
    members: list[str] = []
    for plate in root.findall("plate"):
        for metadata in plate.findall("metadata"):
            if metadata.attrib.get("key") != "gcode_file":
                continue
            value = metadata.attrib.get("value", "").strip().replace("\\", "/").lstrip("/")
            if value and GCODE_MEMBER_RE.match(value):
                members.append(value)
            break
    return members


def safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _sum_optional(values: Iterable[float | None]) -> float | None:
    total = 0.0
    found = False
    for value in values:
        if value is not None:
            total += value
            found = True
    return total if found else None


def read_3mf_summary(source_3mf: Path) -> ThreeMfSummary:
    with zipfile.ZipFile(source_3mf, "r") as archive:
        members = list_gcode_members(archive)
        if not members:
            raise ValueError(f"No Metadata/plate_N.gcode member was found in {source_3mf}")
        plate_metadata = read_slice_plate_metadata(archive)
        filament_metadata = read_filament_metadata(archive)

    predictions: list[float | None] = []
    weights: list[float | None] = []
    used_m_values: list[float | None] = []
    used_g_values: list[float | None] = []
    for member in members:
        match = GCODE_MEMBER_RE.match(member)
        plate_index = int(match.group(1)) if match else 1
        metadata = plate_metadata.get(plate_index, {})
        filament = filament_metadata.get(plate_index, {})
        prediction = safe_float(metadata.get("prediction"))
        weight = safe_float(metadata.get("weight"))
        used_m = safe_float(filament.get("used_m"))
        used_g = safe_float(filament.get("used_g"))
        predictions.append(prediction)
        weights.append(weight if weight is not None else used_g)
        used_m_values.append(used_m)
        used_g_values.append(used_g if used_g is not None else weight)

    return ThreeMfSummary(
        source_3mf=source_3mf,
        plate_count=len(members),
        prediction_seconds=_sum_optional(predictions),
        weight_grams=_sum_optional(weights),
        filament_used_m=_sum_optional(used_m_values),
        filament_used_g=_sum_optional(used_g_values),
    )


def multiply_summary(summary: ThreeMfSummary, copies: int) -> BuildSummary:
    multiplier = max(1, int(copies))
    return BuildSummary(
        plate_count=summary.plate_count * multiplier,
        total_prediction_seconds=None if summary.prediction_seconds is None else summary.prediction_seconds * multiplier,
        total_weight_grams=None if summary.weight_grams is None else summary.weight_grams * multiplier,
        total_filament_used_m=None if summary.filament_used_m is None else summary.filament_used_m * multiplier,
        total_filament_used_g=None if summary.filament_used_g is None else summary.filament_used_g * multiplier,
    )


def summarize_jobs(jobs: Iterable[PlateJob]) -> BuildSummary:
    plate_count = 0
    predictions: list[float | None] = []
    weights: list[float | None] = []
    used_m_values: list[float | None] = []
    used_g_values: list[float | None] = []
    for job in jobs:
        summary = multiply_summary(read_3mf_summary(job.source_3mf), job.copies)
        plate_count += summary.plate_count
        predictions.append(summary.total_prediction_seconds)
        weights.append(summary.total_weight_grams)
        used_m_values.append(summary.total_filament_used_m)
        used_g_values.append(summary.total_filament_used_g)
    return BuildSummary(
        plate_count=plate_count,
        total_prediction_seconds=_sum_optional(predictions),
        total_weight_grams=_sum_optional(weights),
        total_filament_used_m=_sum_optional(used_m_values),
        total_filament_used_g=_sum_optional(used_g_values),
    )


def update_first_slice_info(base_data: bytes, sources: list[PlateSource], options: BuildOptions) -> bytes:
    if options.metadata_mode == "source":
        return base_data
    try:
        root = ET.fromstring(base_data.decode("utf-8-sig", errors="replace"))
    except Exception:
        return base_data
    total_prediction = sum(value for value in (source.prediction_seconds for source in sources) if value is not None)
    total_weight = sum(value for value in (source.weight_grams for source in sources) if value is not None)
    total_used_m = sum(value for value in (source.filament_used_m for source in sources) if value is not None)
    total_used_g = sum(value for value in (source.filament_used_g for source in sources) if value is not None)
    first_plate = root.find("plate")
    if first_plate is None:
        return base_data
    for metadata in first_plate.findall("metadata"):
        key = metadata.attrib.get("key")
        if key == "prediction" and total_prediction > 0:
            metadata.set("value", str(int(round(total_prediction))))
        elif key == "weight" and total_weight > 0:
            metadata.set("value", f"{total_weight:.2f}")
    filament = first_plate.find("filament")
    if filament is not None:
        if total_used_m > 0:
            filament.set("used_m", f"{total_used_m:.2f}")
        if total_used_g > 0:
            filament.set("used_g", f"{total_used_g:.2f}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
