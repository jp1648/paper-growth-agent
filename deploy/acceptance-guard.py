"""Root supervisor guard: restore the durable pause before restarting the listener."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def copy_pause(source, destination):
    metadata = source.stat()
    data = source.read_bytes()
    fd, temporary = tempfile.mkstemp(prefix=".pause-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), metadata.st_mode & 0o777)
            os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cleanup(pause, backup):
    # Stop the child first; a live child could otherwise remove the restored pause.
    result = subprocess.run(
        ["/usr/bin/docker", "ps", "-q", "--filter", "name=^trader-acceptance$"],
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout.strip():
        subprocess.run(["/usr/bin/docker", "stop", "--time", "30", "trader-acceptance"], check=True)
    copy_pause(backup, pause)
    subprocess.run(["/usr/bin/systemctl", "enable", "--now", "always-on.service"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["save", "cleanup"])
    parser.add_argument("pause", type=Path)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    if args.mode == "save":
        # A failed prepare must not restore a previous run's stale marker.
        args.backup.unlink(missing_ok=True)
        copy_pause(args.pause, args.backup)
    else:
        cleanup(args.pause, args.backup)


if __name__ == "__main__":
    main()
