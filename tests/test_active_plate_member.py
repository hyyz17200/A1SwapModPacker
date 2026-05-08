from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from a1_swap_mod_packer.builder import (
    normalized_zip_compress_level,
    preview_members_for_gcode_member,
    resolve_output_gcode_member,
    select_preview_sources,
    write_output_3mf,
)
from a1_swap_mod_packer.metadata import read_model_settings_gcode_members
from a1_swap_mod_packer.models import BuildOptions, PlateSource

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


class ActivePlateMemberTest(unittest.TestCase):
    def test_write_output_preserves_configured_plate_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_3mf = root / "plate2.3mf"
            output_3mf = root / "out.3mf"
            self.write_plate2_archive(source_3mf)

            with zipfile.ZipFile(source_3mf, "r") as archive:
                self.assertEqual(read_model_settings_gcode_members(archive), ["Metadata/plate_2.gcode"])
                self.assertEqual(resolve_output_gcode_member(archive, "Metadata/plate_2.gcode"), "Metadata/plate_2.gcode")
                self.assertEqual(
                    preview_members_for_gcode_member("Metadata/plate_2.gcode"),
                    {"Metadata/plate_2.png", "Metadata/plate_2_small.png"},
                )

            gcode_bytes = b"G1 X1\n"
            sources = [
                PlateSource(
                    source_3mf=source_3mf,
                    member_name="Metadata/plate_2.gcode",
                    gcode_text="G1 X0\n",
                )
            ]
            options = BuildOptions(
                swap_gcode="",
                output_3mf=output_3mf,
                add_preview_label=False,
                apply_gcode_patches=False,
            )
            md5 = write_output_3mf(source_3mf, output_3mf, gcode_bytes, sources, options)

            with zipfile.ZipFile(output_3mf, "r") as archive:
                names = set(archive.namelist())
                self.assertIn("Metadata/plate_2.gcode", names)
                self.assertIn("Metadata/plate_2.gcode.md5", names)
                self.assertNotIn("Metadata/plate_1.gcode", names)
                self.assertEqual(archive.read("Metadata/plate_2.gcode"), gcode_bytes)
                self.assertEqual(archive.read("Metadata/plate_2.gcode.md5").decode(), md5)
                self.assertEqual(md5, hashlib.md5(gcode_bytes).hexdigest())

    def test_write_output_honors_zip_compression_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_3mf = root / "plate2.3mf"
            level_1_output = root / "level1.3mf"
            level_7_output = root / "level7.3mf"
            self.write_plate2_archive(source_3mf)
            sources = [
                PlateSource(
                    source_3mf=source_3mf,
                    member_name="Metadata/plate_2.gcode",
                    gcode_text="G1 X0\n",
                )
            ]
            gcode_bytes = (b"G1 X123.456 Y789.012 E0.03456 ; repeated movement\n" * 20000)

            for output_3mf, zip_level in ((level_1_output, 1), (level_7_output, 7)):
                options = BuildOptions(
                    swap_gcode="",
                    output_3mf=output_3mf,
                    add_preview_label=False,
                    apply_gcode_patches=False,
                    zip_compress_level=zip_level,
                )
                write_output_3mf(source_3mf, output_3mf, gcode_bytes, sources, options)

            with zipfile.ZipFile(level_1_output, "r") as level_1_archive, zipfile.ZipFile(level_7_output, "r") as level_7_archive:
                level_1_info = level_1_archive.getinfo("Metadata/plate_2.gcode")
                level_7_info = level_7_archive.getinfo("Metadata/plate_2.gcode")
                self.assertEqual(level_1_info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(level_7_info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertLess(level_7_info.compress_size, level_1_info.compress_size)

    def test_zip_compression_level_is_clamped(self) -> None:
        self.assertEqual(normalized_zip_compress_level(None), 7)
        self.assertEqual(normalized_zip_compress_level(0), 1)
        self.assertEqual(normalized_zip_compress_level(10), 9)

    def test_preview_composite_uses_first_nine_unique_input_files(self) -> None:
        if Image is None:
            self.skipTest("Pillow is not installed")

        colors = [
            (255, 0, 0, 255),
            (0, 0, 255, 255),
            (255, 255, 0, 255),
            (255, 0, 255, 255),
            (0, 255, 255, 255),
            (128, 0, 0, 255),
            (0, 0, 128, 255),
            (128, 128, 0, 255),
            (128, 0, 128, 255),
            (255, 128, 0, 255),
        ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources: list[PlateSource] = []
            for index, color in enumerate(colors):
                source_3mf = root / f"source_{index}.3mf"
                self.write_plate2_archive(source_3mf, image_color=color, image_size=300)
                sources.append(
                    PlateSource(
                        source_3mf=source_3mf,
                        member_name="Metadata/plate_2.gcode",
                        gcode_text="G1 X0\n",
                    )
                )

            selected = select_preview_sources(sources)
            self.assertEqual(len(selected), 9)
            self.assertEqual(selected[-1].source_3mf.name, "source_8.3mf")

            output_3mf = root / "out.3mf"
            options = BuildOptions(
                swap_gcode="",
                output_3mf=output_3mf,
                add_preview_label=True,
                apply_gcode_patches=False,
            )
            write_output_3mf(sources[0].source_3mf, output_3mf, b"G1 X1\n", sources, options)

            with zipfile.ZipFile(output_3mf, "r") as archive:
                image_bytes = archive.read("Metadata/plate_2.png")

            import io

            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            sampled_colors = {
                image.getpixel((50, 50)),
                image.getpixel((150, 50)),
                image.getpixel((250, 50)),
                image.getpixel((50, 150)),
                image.getpixel((150, 150)),
                image.getpixel((250, 150)),
                image.getpixel((50, 250)),
                image.getpixel((150, 250)),
                image.getpixel((250, 250)),
            }
            self.assertTrue(set(colors[:9]).issubset(sampled_colors))
            self.assertNotIn(colors[9], sampled_colors)

    @staticmethod
    def write_plate2_archive(path: Path, image_color: tuple[int, int, int, int] | None = None, image_size: int = 128) -> None:
        model_settings = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="gcode_file" value=""/>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <metadata key="gcode_file" value="Metadata/plate_2.gcode"/>
  </plate>
</config>
"""
        slice_info = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="2"/>
  </plate>
</config>
"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Metadata/model_settings.config", model_settings)
            archive.writestr("Metadata/slice_info.config", slice_info)
            archive.writestr("Metadata/plate_2.gcode", "G1 X0\n")
            archive.writestr("Metadata/plate_2.gcode.md5", "old")
            if image_color is None or Image is None:
                archive.writestr("Metadata/plate_2.png", b"png")
                archive.writestr("Metadata/plate_2_small.png", b"png")
            else:
                import io

                image = Image.new("RGBA", (image_size, image_size), image_color)
                output = io.BytesIO()
                image.save(output, format="PNG")
                archive.writestr("Metadata/plate_2.png", output.getvalue())
                small = image.resize((max(1, image_size // 2), max(1, image_size // 2)))
                output = io.BytesIO()
                small.save(output, format="PNG")
                archive.writestr("Metadata/plate_2_small.png", output.getvalue())


if __name__ == "__main__":
    unittest.main()
