from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .archive import GCODE_MEMBER_RE, MD5_MEMBER_RE, list_gcode_members
from .gcode import (
    apply_line_ending,
    apply_plate_number_offset,
    build_swap_block,
    insert_swap_block,
    normalize_newlines,
    read_swap_gcode_file,
    resolve_swap_gcode_path,
)
from .metadata import (
    read_filament_metadata,
    read_model_settings_gcode_members,
    read_slice_plate_metadata,
    safe_float,
    update_first_slice_info,
)
from .models import BuildOptions, BuildResult, GcodePatchConfig, PlateJob, PlateSource
from .patches import apply_gcode_patches, parse_patch_config

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None

PREVIEW_MEMBER_RE = re.compile(r"^Metadata/plate_(\d+)(?:_small)?\.png$", re.IGNORECASE)
MAX_COMPOSITE_PREVIEW_INPUTS = 9


def load_plate_sources(job: PlateJob) -> list[PlateSource]:
    if job.copies < 1:
        raise ValueError(f"Invalid copy count for {job.source_3mf}: {job.copies}")
    with zipfile.ZipFile(job.source_3mf, "r") as archive:
        members = list_gcode_members(archive)
        if not members:
            raise ValueError(f"No Metadata/plate_N.gcode member was found in {job.source_3mf}")
        plate_metadata = read_slice_plate_metadata(archive)
        filament_metadata = read_filament_metadata(archive)
        sources: list[PlateSource] = []
        for member in members:
            match = GCODE_MEMBER_RE.match(member)
            plate_index = int(match.group(1)) if match else 1
            raw = archive.read(member)
            text = raw.decode("utf-8-sig", errors="replace")
            metadata = plate_metadata.get(plate_index, {})
            filament = filament_metadata.get(plate_index, {})
            weight = safe_float(metadata.get("weight"))
            used_g = safe_float(filament.get("used_g"))
            sources.append(
                PlateSource(
                    source_3mf=job.source_3mf,
                    member_name=member,
                    gcode_text=text,
                    prediction_seconds=safe_float(metadata.get("prediction")),
                    weight_grams=weight if weight is not None else used_g,
                    filament_used_m=safe_float(filament.get("used_m")),
                    filament_used_g=used_g if used_g is not None else weight,
                )
            )
        return sources


def expand_jobs(jobs: list[PlateJob]) -> list[PlateSource]:
    expanded: list[PlateSource] = []
    for job in jobs:
        sources = load_plate_sources(job)
        for _ in range(job.copies):
            expanded.extend(sources)
    if not expanded:
        raise ValueError("No input plates were provided.")
    return expanded


def process_plate_gcode(
    source: PlateSource,
    plate_number: int,
    total_plates: int,
    options: BuildOptions,
    swap_gcode_text: str,
    patch_config: GcodePatchConfig,
) -> str:
    text = normalize_newlines(source.gcode_text)
    if options.apply_gcode_patches:
        text = apply_gcode_patches(text, patch_config)
    if options.show_plate_number:
        text = apply_plate_number_offset(text, plate_number)
    should_swap = options.swap_after_final or plate_number < total_plates
    if should_swap:
        swap_block = build_swap_block(swap_gcode_text, options.cool_bed_temp, options.wait_after_eject_seconds)
        text = insert_swap_block(text, swap_block, patch_config.insert_before_marker)
    return text.rstrip("\n") + "\n"


def save_png_bytes(image: object) -> bytes:
    import io

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def update_preview_label(png_bytes: bytes, label: str, small: bool = False) -> bytes:
    if Image is None or ImageDraw is None:
        return png_bytes
    import io

    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return png_bytes
    draw = ImageDraw.Draw(image)
    size = max(16, image.width // (12 if small else 8))
    font = None
    for font_path in (
        Path(os.environ.get("WINDIR", "")) / "Fonts" / "arialbd.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ):
        if font_path.exists() and ImageFont is not None:
            try:
                font = ImageFont.truetype(str(font_path), size)
                break
            except Exception:
                font = None
    if font is None and ImageFont is not None:
        font = ImageFont.load_default()
    margin = max(8, image.width // 24)
    text_bbox = draw.textbbox((0, 0), label, font=font)
    x = margin
    y = image.height - margin - (text_bbox[3] - text_bbox[1])
    draw.text((x, y), label, font=font, fill=(0, 255, 0, 255))
    return save_png_bytes(image)


def preview_member_name_for_gcode_member(gcode_member: str, small: bool = False) -> str | None:
    match = GCODE_MEMBER_RE.match(gcode_member)
    if not match:
        return None
    suffix = "_small" if small else ""
    return f"Metadata/plate_{match.group(1)}{suffix}.png"


def preview_member_sort_key(member_name: str) -> tuple[int, int, str]:
    match = PREVIEW_MEMBER_RE.match(member_name)
    plate_number = int(match.group(1)) if match else 9999
    is_small = 1 if member_name.lower().endswith("_small.png") else 0
    return (is_small, plate_number, member_name.lower())


def select_preview_sources(sources: list[PlateSource], max_inputs: int = MAX_COMPOSITE_PREVIEW_INPUTS) -> list[PlateSource]:
    selected: list[PlateSource] = []
    seen_paths: set[Path] = set()
    for source in sources:
        key = source.source_3mf.resolve(strict=False)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        selected.append(source)
        if len(selected) >= max_inputs:
            break
    return selected


def source_preview_member(archive: zipfile.ZipFile, source: PlateSource, small: bool = False) -> str | None:
    names = set(archive.namelist())
    active_member = resolve_output_gcode_member(archive, source.member_name)
    preferred = preview_member_name_for_gcode_member(active_member, small)
    if preferred in names:
        return preferred
    alternate = preview_member_name_for_gcode_member(active_member, not small)
    if alternate in names:
        return alternate

    candidates = [
        name
        for name in names
        if PREVIEW_MEMBER_RE.match(name) and name.lower().endswith("_small.png") == small
    ]
    if not candidates:
        candidates = [name for name in names if PREVIEW_MEMBER_RE.match(name)]
    if not candidates:
        candidates = [
            name
            for name in names
            if name.lower().startswith("metadata/") and name.lower().endswith(".png")
        ]
    if not candidates:
        return None
    return sorted(candidates, key=preview_member_sort_key)[0]


def read_source_preview_image(source: PlateSource, small: bool = False) -> object | None:
    if Image is None:
        return None
    import io

    try:
        with zipfile.ZipFile(source.source_3mf, "r") as archive:
            member = source_preview_member(archive, source, small)
            if member is None:
                return None
            data = archive.read(member)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def preview_grid_dimensions(count: int) -> tuple[int, int]:
    if count <= 1:
        return (1, 1)
    if count <= 2:
        return (2, 1)
    if count <= 4:
        return (2, 2)
    return (3, (count + 2) // 3)


def resize_image_to_fit(image: object, width: int, height: int) -> object:
    resized = image.copy()
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    resized.thumbnail((max(1, width), max(1, height)), resampling)
    return resized


def compose_preview_image(png_bytes: bytes, sources: list[PlateSource], label: str, small: bool = False) -> bytes:
    if Image is None:
        return png_bytes
    import io

    try:
        base_image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return update_preview_label(png_bytes, label, small)

    source_images = [
        image
        for image in (read_source_preview_image(source, small) for source in select_preview_sources(sources))
        if image is not None
    ]
    if not source_images:
        return update_preview_label(png_bytes, label, small)

    width, height = base_image.size
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    columns, rows = preview_grid_dimensions(len(source_images))
    for index, image in enumerate(source_images):
        column = index % columns
        row = index // columns
        left = width * column // columns
        right = width * (column + 1) // columns
        top = height * row // rows
        bottom = height * (row + 1) // rows
        fitted = resize_image_to_fit(image, right - left, bottom - top)
        x = left + max(0, (right - left - fitted.width) // 2)
        y = top + max(0, (bottom - top - fitted.height) // 2)
        canvas.paste(fitted, (x, y), fitted)

    return update_preview_label(save_png_bytes(canvas), label, small)


def resolve_output_gcode_member(archive: zipfile.ZipFile, fallback_member: str) -> str:
    configured_members = read_model_settings_gcode_members(archive)
    if fallback_member in configured_members:
        return fallback_member
    existing_members = set(list_gcode_members(archive))
    for member in configured_members:
        if member in existing_members:
            return member
    if configured_members:
        return configured_members[0]
    return fallback_member


def preview_members_for_gcode_member(gcode_member: str) -> set[str]:
    full_member = preview_member_name_for_gcode_member(gcode_member, small=False)
    small_member = preview_member_name_for_gcode_member(gcode_member, small=True)
    if full_member is None or small_member is None:
        return set()
    return {full_member, small_member}


def write_output_3mf(base_3mf: Path, output_3mf: Path, gcode_bytes: bytes, sources: list[PlateSource], options: BuildOptions) -> str:
    md5 = hashlib.md5(gcode_bytes).hexdigest()
    with zipfile.ZipFile(base_3mf, "r") as src, zipfile.ZipFile(output_3mf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as dst:
        gcode_member = resolve_output_gcode_member(src, sources[0].member_name)
        preview_members = preview_members_for_gcode_member(gcode_member)
        for item in src.infolist():
            name = item.filename
            if GCODE_MEMBER_RE.match(name) or MD5_MEMBER_RE.match(name):
                continue
            data = src.read(name)
            if name == "Metadata/slice_info.config":
                data = update_first_slice_info(data, sources, options)
            elif options.add_preview_label and name in preview_members:
                data = compose_preview_image(data, sources, f"{len(sources)} P", small=name.endswith("_small.png"))
            dst.writestr(item, data)
        dst.writestr(gcode_member, gcode_bytes)
        dst.writestr(f"{gcode_member}.md5", md5)
    return md5


def build_packed_3mf(jobs: list[PlateJob], options: BuildOptions) -> BuildResult:
    sources = expand_jobs(jobs)
    swap_gcode_path = resolve_swap_gcode_path(options.swap_gcode, options.swap_gcode_dir)
    swap_gcode_text = read_swap_gcode_file(swap_gcode_path)
    patch_config = parse_patch_config() if options.apply_gcode_patches else GcodePatchConfig(rules=())
    processed: list[str] = []
    for plate_number, source in enumerate(sources, start=1):
        processed.append(process_plate_gcode(source, plate_number, len(sources), options, swap_gcode_text, patch_config))
    final_gcode = "\n".join(item.rstrip("\n") for item in processed) + "\n"
    gcode_bytes = apply_line_ending(final_gcode, options.line_ending)
    output_3mf = options.output_3mf
    output_3mf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".3mf", dir=str(output_3mf.parent)) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        md5 = write_output_3mf(sources[0].source_3mf, temp_path, gcode_bytes, sources, options)
        shutil.move(str(temp_path), str(output_3mf))
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
    total_prediction = sum(value for value in (source.prediction_seconds for source in sources) if value is not None)
    total_weight = sum(value for value in (source.weight_grams for source in sources) if value is not None)
    return BuildResult(
        output_3mf=output_3mf,
        plate_count=len(sources),
        total_prediction_seconds=total_prediction if total_prediction > 0 else None,
        total_weight_grams=total_weight if total_weight > 0 else None,
        gcode_md5=md5,
    )
