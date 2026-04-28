from __future__ import annotations

import re
import zipfile

GCODE_MEMBER_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
MD5_MEMBER_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode\.md5$")


def list_gcode_members(archive: zipfile.ZipFile) -> list[str]:
    members: list[tuple[int, str]] = []
    for name in archive.namelist():
        match = GCODE_MEMBER_RE.match(name)
        if match:
            members.append((int(match.group(1)), name))
    return [name for _, name in sorted(members)]
