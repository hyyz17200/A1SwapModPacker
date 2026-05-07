from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from a1_swap_mod_packer.builder import (
    preview_members_for_gcode_member,
    resolve_output_gcode_member,
    write_output_3mf,
)
from a1_swap_mod_packer.metadata import read_model_settings_gcode_members
from a1_swap_mod_packer.models import BuildOptions, PlateSource


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

    @staticmethod
    def write_plate2_archive(path: Path) -> None:
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
            archive.writestr("Metadata/plate_2.png", b"png")
            archive.writestr("Metadata/plate_2_small.png", b"png")


if __name__ == "__main__":
    unittest.main()
