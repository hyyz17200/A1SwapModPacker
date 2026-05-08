from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import a1_swap_mod_packer.builder as builder
from a1_swap_mod_packer.models import BuildOptions, GcodePatchConfig, GcodePatchRule, PlateJob


class GcodePrepareCacheTest(unittest.TestCase):
    def test_repeated_copies_prepare_gcode_once_per_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_3mf = root / "source.3mf"
            output_3mf = root / "out.3mf"
            swap_gcode = root / "swap.gcode"
            self.write_archive(source_3mf)
            swap_gcode.write_text("; swap\nG1 X5\n", encoding="utf-8")

            patch_config = GcodePatchConfig(
                rules=(
                    GcodePatchRule(
                        name="test",
                        find="G0 Y254 F3000",
                        replace="G0 Y250 F3000 ;Patched",
                    ),
                )
            )
            patch_calls = 0
            captured_gcode = ""

            def count_patch_calls(text: str, config: GcodePatchConfig) -> str:
                nonlocal patch_calls
                patch_calls += 1
                return text.replace("G0 Y254 F3000", "G0 Y250 F3000 ;Patched", 1)

            def capture_gcode(
                base_3mf: Path,
                output_3mf: Path,
                gcode_bytes: bytes,
                sources: list[builder.PlateSource],
                options: BuildOptions,
            ) -> str:
                nonlocal captured_gcode
                captured_gcode = gcode_bytes.decode("utf-8")
                return hashlib.md5(gcode_bytes).hexdigest()

            options = BuildOptions(
                swap_gcode=swap_gcode,
                output_3mf=output_3mf,
                apply_gcode_patches=True,
                show_plate_number=False,
                swap_after_final=False,
                line_ending="lf",
                add_preview_label=False,
            )

            with patch.object(builder, "parse_patch_config", return_value=patch_config), patch.object(
                builder,
                "apply_gcode_patches",
                side_effect=count_patch_calls,
            ), patch.object(builder, "write_output_3mf", side_effect=capture_gcode):
                result = builder.build_packed_3mf([PlateJob(source_3mf, 3)], options)

            self.assertEqual(result.plate_count, 3)
            self.assertEqual(patch_calls, 1)
            self.assertEqual(captured_gcode.count("G0 Y250 F3000 ;Patched"), 3)
            self.assertNotIn("G0 Y254 F3000", captured_gcode)

    def test_repeated_copies_prepare_m73_template_once_per_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_3mf = root / "source.3mf"
            output_3mf = root / "out.3mf"
            swap_gcode = root / "swap.gcode"
            self.write_archive(source_3mf)
            swap_gcode.write_text("; swap\nG1 X5\n", encoding="utf-8")

            m73_template_calls = 0
            captured_gcode = ""
            original_prepare_m73 = builder.prepare_m73_offset_template

            def count_m73_template_calls(text: str):
                nonlocal m73_template_calls
                m73_template_calls += 1
                return original_prepare_m73(text)

            def capture_gcode(
                base_3mf: Path,
                output_3mf: Path,
                gcode_bytes: bytes,
                sources: list[builder.PlateSource],
                options: BuildOptions,
            ) -> str:
                nonlocal captured_gcode
                captured_gcode = gcode_bytes.decode("utf-8")
                return hashlib.md5(gcode_bytes).hexdigest()

            options = BuildOptions(
                swap_gcode=swap_gcode,
                output_3mf=output_3mf,
                apply_gcode_patches=False,
                show_plate_number=True,
                swap_after_final=False,
                line_ending="lf",
                add_preview_label=False,
            )

            with (
                patch.object(builder, "prepare_m73_offset_template", side_effect=count_m73_template_calls),
                patch.object(
                    builder,
                    "write_output_3mf",
                    side_effect=capture_gcode,
                ),
            ):
                result = builder.build_packed_3mf([PlateJob(source_3mf, 3)], options)

            self.assertEqual(result.plate_count, 3)
            self.assertEqual(m73_template_calls, 1)
            self.assertIn("M73 P0 R6010", captured_gcode)
            self.assertIn("M73 P0 R12010", captured_gcode)
            self.assertIn("M73 P0 R18010", captured_gcode)

    @staticmethod
    def write_archive(path: Path) -> None:
        slice_info = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
  </plate>
</config>
"""
        gcode = """G0 X128 F30000
G0 Y254 F3000
M73 P0 R10
;=====printer finish  sound=========
"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Metadata/slice_info.config", slice_info)
            archive.writestr("Metadata/plate_1.gcode", gcode)


if __name__ == "__main__":
    unittest.main()
