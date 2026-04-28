from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .metadata import read_3mf_summary
from .models import PlateJob, ThreeMfSummary

DEFAULT_OUTPUT_PATTERN = "{plates} Plates - {sources}.3mf"

SummaryResolver = Callable[[Path], ThreeMfSummary]


@dataclass(frozen=True)
class OutputSummary:
    plate_count: int
    copy_count: int
    prediction_seconds: float | None = None
    weight_grams: float | None = None
    filament_used_m: float | None = None


@dataclass(frozen=True)
class OutputNamingOptions:
    output_directory: Path | str | None = None
    filename_rule: str = DEFAULT_OUTPUT_PATTERN


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def three_mf_summary_from_mapping(data: Mapping[str, object]) -> ThreeMfSummary:
    source_value = data.get("source_3mf")
    return ThreeMfSummary(
        source_3mf=Path(str(source_value)) if source_value is not None else Path(),
        plate_count=int(data.get("plate_count") or 0),
        prediction_seconds=_optional_float(data.get("prediction_seconds")),
        weight_grams=_optional_float(data.get("weight_grams")),
        filament_used_m=_optional_float(data.get("filament_used_m")),
        filament_used_g=_optional_float(data.get("filament_used_g")),
    )


def summarize_jobs_for_output(
    jobs: Sequence[PlateJob],
    summary_resolver: SummaryResolver = read_3mf_summary,
) -> OutputSummary:
    plate_count = 0
    copy_count = 0
    prediction_total = 0.0
    prediction_found = False
    weight_total = 0.0
    weight_found = False
    used_m_total = 0.0
    used_m_found = False
    for job in jobs:
        summary = summary_resolver(job.source_3mf)
        copies = max(1, int(job.copies))
        copy_count += copies
        plate_count += summary.plate_count * copies
        if summary.prediction_seconds is not None:
            prediction_total += summary.prediction_seconds * copies
            prediction_found = True
        if summary.weight_grams is not None:
            weight_total += summary.weight_grams * copies
            weight_found = True
        if summary.filament_used_m is not None:
            used_m_total += summary.filament_used_m * copies
            used_m_found = True
    return OutputSummary(
        plate_count=plate_count,
        copy_count=copy_count,
        prediction_seconds=prediction_total if prediction_found else None,
        weight_grams=weight_total if weight_found else None,
        filament_used_m=used_m_total if used_m_found else None,
    )


def sanitize_filename(file_name: str) -> str:
    sanitized = re.sub(r"[\\/:*?\"<>|]+", "_", file_name).strip()
    sanitized = sanitized.rstrip(". ")
    return sanitized or "packed.3mf"


def resolve_output_path(
    jobs: Sequence[PlateJob],
    naming: OutputNamingOptions,
    summary_resolver: SummaryResolver = read_3mf_summary,
    now: datetime | None = None,
) -> Path:
    if not jobs:
        raise ValueError("No input 3MF file was provided.")
    summary = summarize_jobs_for_output(jobs, summary_resolver)
    first_input = jobs[0].source_3mf
    stems = [job.source_3mf.stem for job in jobs]
    unique_stems = list(dict.fromkeys(stems))
    source_token = first_input.stem
    sources_token = source_token if len(unique_stems) == 1 else f"{source_token}_and_{len(unique_stems) - 1}_more"
    timestamp = now or datetime.now()
    tokens = SafeFormatDict(
        source=source_token,
        sources=sources_token,
        plates=summary.plate_count,
        copies=summary.copy_count,
        date=timestamp.strftime("%Y%m%d"),
        time=timestamp.strftime("%H%M%S"),
    )
    pattern = naming.filename_rule.strip() or DEFAULT_OUTPUT_PATTERN
    file_name = sanitize_filename(pattern.format_map(tokens))
    if not file_name.lower().endswith(".3mf"):
        file_name += ".3mf"
    output_dir_text = "" if naming.output_directory is None else str(naming.output_directory).strip()
    output_dir = Path(output_dir_text) if output_dir_text else first_input.parent
    return output_dir / file_name


def make_unique_for_run(path: Path, used_paths: set[Path]) -> Path:
    resolved_key = path.resolve(strict=False)
    if resolved_key not in used_paths:
        used_paths.add(resolved_key)
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        candidate_key = candidate.resolve(strict=False)
        if candidate_key not in used_paths:
            used_paths.add(candidate_key)
            return candidate
        index += 1


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
