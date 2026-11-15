"""Verify the pinned Go module archive and stage only the offline converter."""

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

module, destination = sys.argv[1:]
destination = Path(destination)
metadata = json.load(sys.stdin)
pinned = "h1:EwKf71x3EfUaF6PsZCiBWjSejs8lZh5WH9U0mV2UXSE="

# Match Go's dirhash.HashZip: hash the sorted names and individual contents.
# This checks the actual archive even if the local module cache was modified.
with zipfile.ZipFile(metadata["Zip"]) as archive:
    digest = hashlib.sha256()
    for name in sorted(archive.namelist()):
        file_digest = hashlib.sha256(archive.read(name)).hexdigest()
        digest.update(f"{file_digest}  {name}\n".encode("utf-8"))
    checksum = "h1:" + base64.b64encode(digest.digest()).decode("ascii")
    if checksum != pinned or metadata["Sum"] != pinned:
        sys.exit("Unexpected Kindle converter checksum")

    for name in archive.namelist():
        relative = PurePosixPath(name.removeprefix(module + "/"))
        if relative != PurePosixPath("LICENSE") and not (
            relative.parent.as_posix() in {"internal/azw3", "internal/epub"}
            and relative.suffix == ".go"
            and not relative.name.endswith("_test.go")
        ):
            continue
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(archive.read(name))
