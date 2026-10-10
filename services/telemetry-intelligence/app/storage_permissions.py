"""Prepare an existing M1 volume for its nonroot runtime without deleting data."""
import os
from pathlib import Path
import sys


def prepare(root, uid=10001, gid=10001):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("The M1 data root must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    for directory, children, files in os.walk(root, followlinks=False):
        path = Path(directory)
        os.chown(path, uid, gid)
        os.chmod(path, 0o2770)
        children[:] = [name for name in children if not (path / name).is_symlink()]
        for name in files:
            entry = path / name
            if not entry.is_symlink():
                os.chown(entry, uid, gid)
                os.chmod(entry, 0o660)


if __name__ == "__main__":
    prepare(sys.argv[1] if len(sys.argv) > 1 else "/data")
    print("M1 persistent data prepared for uid 10001")
