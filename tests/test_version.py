from __future__ import annotations

import contextlib
import io
import unittest

from a1_swap_mod_packer import APP_NAME, APP_TITLE, __version__
from a1_swap_mod_packer.cli import create_parser


class VersionDisplayTest(unittest.TestCase):
    def test_app_title_uses_shared_version(self) -> None:
        self.assertEqual(APP_TITLE, f"{APP_NAME} v{__version__}")

    def test_cli_help_and_version_use_shared_version(self) -> None:
        parser = create_parser()
        self.assertIn(APP_TITLE, parser.format_help())

        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(stdout):
            parser.parse_args(["--version"])

        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"{APP_NAME} {__version__}")


if __name__ == "__main__":
    unittest.main()
