from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, APP_TITLE, __version__
from .core import (
    BuildOptions,
    DEFAULT_ZIP_COMPRESS_LEVEL,
    PlateJob,
    build_packed_3mf,
    list_swap_gcode_files,
)
from .paths import default_patch_config_path, default_swap_gcode_dir


def parse_item(values: list[str]) -> PlateJob:
    if len(values) != 2:
        raise argparse.ArgumentTypeError("Each --item needs a path and a copy count.")
    path = Path(values[0])
    try:
        copies = int(values[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid copy count: {values[1]}") from exc
    return PlateJob(path, copies)


def build_command(args: argparse.Namespace) -> int:
    jobs: list[PlateJob] = []
    for item in args.item or []:
        jobs.append(parse_item(item))
    for input_path in args.inputs or []:
        jobs.append(PlateJob(Path(input_path), args.copies))
    if not jobs:
        raise SystemExit("No input 3MF file was provided.")
    cool_bed_temp = None if args.no_bed_cooldown else args.cool_bed
    options = BuildOptions(
        swap_gcode=args.swap_gcode,
        output_3mf=Path(args.output),
        cool_bed_temp=cool_bed_temp,
        wait_after_eject_seconds=args.wait,
        show_plate_number=args.show_plate_number,
        swap_after_final=not args.no_swap_after_final,
        metadata_mode=args.metadata_mode,
        line_ending=args.line_ending,
        add_preview_label=not args.no_preview_label,
        apply_gcode_patches=not args.no_gcode_patches,
        swap_gcode_dir=Path(args.swap_gcode_dir) if args.swap_gcode_dir else None,
        zip_compress_level=args.zip_level,
    )
    result = build_packed_3mf(jobs, options)
    print(f"Output: {result.output_3mf}")
    print(f"Plates: {result.plate_count}")
    print(f"G-code MD5: {result.gcode_md5}")
    if result.total_prediction_seconds is not None:
        print(f"Source print time: {int(result.total_prediction_seconds)} seconds")
    if result.total_weight_grams is not None:
        print(f"Source filament weight: {result.total_weight_grams:.2f} g")
    return 0


def list_swap_gcode_command(args: argparse.Namespace) -> int:
    directory = Path(args.swap_gcode_dir) if args.swap_gcode_dir else default_swap_gcode_dir()
    files = list_swap_gcode_files(directory)
    if not files:
        print(f"No swap G-code files found in: {directory}")
        return 0
    for path in files:
        print(path.name)
    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="a1-swap-mod-packer",
        description=f"{APP_TITLE} - Pack repeated Bambu A1 SwapMod plates into one 3MF job.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a packed 3MF file.")
    build.add_argument("inputs", nargs="*", help="Input 3MF files. Use --copies for the same copy count on all of them.")
    build.add_argument("--item", nargs=2, action="append", metavar=("PATH", "COPIES"), help="Add an input 3MF with its own copy count. Can be used multiple times.")
    build.add_argument("-o", "--output", required=True, help="Output 3MF path.")
    build.add_argument("--swap-gcode", required=True, help="Swap G-code file name in swap_gcode, or an explicit file path.")
    build.add_argument("--swap-gcode-dir", default=None, help=f"Template directory. Default: {default_swap_gcode_dir()}")
    build.add_argument("--copies", type=int, default=1, help="Copy count for positional input files.")
    build.add_argument("--cool-bed", type=int, default=45, help="Bed temperature to wait for before running the swap G-code.")
    build.add_argument("--no-bed-cooldown", action="store_true", help="Do not insert M190 before the swap code.")
    build.add_argument("--wait", type=int, default=45, help="Seconds to wait after plate ejection.")
    build.add_argument("--show-plate-number", action="store_true", help="Add 100 hours per plate number to M73 R values.")
    build.add_argument("--no-swap-after-final", action="store_true", help="Do not run the swap G-code after the last plate.")
    build.add_argument("--metadata-mode", choices=("source", "sum"), default="source", help="How to write slice_info prediction and weight.")
    build.add_argument("--line-ending", choices=("lf", "crlf"), default="crlf", help="Line ending for the generated G-code.")
    build.add_argument("--zip-level", type=int, choices=range(1, 10), default=DEFAULT_ZIP_COMPRESS_LEVEL, metavar="1-9", help="zlib-ng Deflate compression level for the output 3MF. Default: 7.")
    build.add_argument("--no-preview-label", action="store_true", help="Do not add a plate-count label to the first preview image.")
    build.add_argument("--no-gcode-patches", action="store_true", help=f"Do not apply editable patches from {default_patch_config_path()}.")
    build.set_defaults(func=build_command)

    list_cmd = subparsers.add_parser("list-swap-gcode", help="List files from the swap_gcode directory.")
    list_cmd.add_argument("--swap-gcode-dir", default=None, help=f"Template directory. Default: {default_swap_gcode_dir()}")
    list_cmd.set_defaults(func=list_swap_gcode_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
