from __future__ import annotations

import unittest

from a1_swap_mod_packer.gcode import M73_RE, apply_plate_number_offset, prepare_m73_offset_template


class M73OffsetTemplateTest(unittest.TestCase):
    def test_template_matches_previous_regex_replacement_semantics(self) -> None:
        gcode = (
            "M73 P0 R10\n"
            "; M73 P0 R20\n"
            " M73 P0 R30\n"
            "M73 P50 R5999 ; comment\n"
            "G1 X1\n"
            "M73 P100 R0\n"
        )

        def previous_apply(text: str, plate_number: int) -> str:
            minute_offset = plate_number * 100 * 60

            def replace(match) -> str:
                prefix = match.group(1)
                remaining_minutes = int(match.group(2)) + minute_offset
                suffix = match.group(3)
                return f"{prefix}{remaining_minutes}{suffix}"

            return M73_RE.sub(replace, text)

        template = prepare_m73_offset_template(gcode)
        for plate_number in (1, 2, 10):
            self.assertEqual(template.apply(plate_number), previous_apply(gcode, plate_number))
            self.assertEqual(apply_plate_number_offset(gcode, plate_number), previous_apply(gcode, plate_number))

    def test_template_without_m73_matches_returns_original_text(self) -> None:
        gcode = "G1 X1\n; M73 P0 R10\n"
        template = prepare_m73_offset_template(gcode)
        self.assertEqual(template.apply(3), gcode)


if __name__ == "__main__":
    unittest.main()
