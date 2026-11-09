import asyncio
import errno
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

from .config import Config

_CONTENTS_PATH = re.compile(r"/ccapi/ver\d+/contents/(.+)")


def media_path(href: str) -> Path:
    """Map an origin-relative CCAPI href to a safe path inside a backup."""
    url = urlsplit(href)
    match = _CONTENTS_PATH.fullmatch(url.path)
    if url.scheme or url.netloc or url.query or url.fragment or not match:
        raise ValueError(f"Invalid CCAPI file path: {href!r}")
    parts = match[1].split("/")
    for part in map(unquote, parts):
        if (
            part in ("", ".", "..")
            or any(char in part for char in '/\\:<>"|?*')
            or any(ord(char) < 32 for char in part)
            or part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
        ):
            raise ValueError(f"Invalid CCAPI file path: {href!r}")
    return Path(*parts)


@dataclass(frozen=True)
class BackupTarget:
    root: Path
    address: tuple[str, int]

    @classmethod
    def from_config(cls, config: Config):
        return cls(Path(os.path.abspath(config.nas_path)), (config.camera_ip.casefold(), config.camera_port))

    def file_id(self, href: str) -> str:
        identity = (str(self.root), self.address, media_path(href).as_posix())
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def checked_path(self, path: Path) -> Path:
        root = self.root.resolve()
        resolved = path.resolve()
        if root not in resolved.parents:
            raise ValueError("File is outside the backup directory")
        return resolved

    def prepare_directory(self, dest: Path) -> None:
        self.checked_path(dest)
        missing = []
        directory = dest.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        dest.parent.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing):
            _sync_directory(created.parent)


async def run_io(fn, *args):
    """Wait for outstanding file I/O before unwinding on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _same_content(left: Path, right: Path) -> bool:
    if not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as source, right.open("rb") as existing:
        while chunk := source.read(1024 * 1024):
            if chunk != existing.read(len(chunk)):
                return False
    return True


def _publish_new(temp: Path, dest: Path) -> None:
    if os.name == "nt":
        os.rename(temp, dest)  # Fails if the destination exists on Windows.
        return
    try:
        os.link(temp, dest)  # Atomically creates the destination without replacing it.
    except OSError as error:
        if error.errno not in (errno.ENOTSUP, errno.ENOSYS, errno.EPERM, errno.EMLINK):
            raise
        # Some network shares lack hard links. Reserve the name without replacing
        # existing files, then atomically replace our reservation.
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            os.replace(temp, dest)
        except OSError:
            dest.unlink(missing_ok=True)
            raise


def publish_backup(
    target: BackupTarget, temp: Path, dest: Path, file_id: str, destination_claimed,
) -> Path:
    """Publish a complete file and keep any existing file with different contents."""
    attempt = 0
    candidate = dest
    while True:
        target.checked_path(candidate)
        if not candidate.is_symlink():
            if not candidate.exists():
                try:
                    _publish_new(temp, candidate)
                except FileExistsError:
                    pass  # Another writer claimed the name after our check.
                else:
                    _sync_directory(candidate.parent)
                    return candidate
            elif not destination_claimed(str(candidate), file_id) and _same_content(temp, candidate):
                # Reuse a complete file left behind before its state was committed.
                with candidate.open("rb+" if os.name == "nt" else "rb") as handle:
                    os.fsync(handle.fileno())
                _sync_directory(candidate.parent)
                return candidate
        attempt += 1
        suffix = f"~{file_id[:12]}" if attempt == 1 else f"~{file_id[:12]}-{attempt}"
        candidate = dest.with_name(f"{dest.stem}{suffix}{dest.suffix}")


def _sync_directory(directory: Path) -> None:
    # Windows does not allow opening a directory with os.open.
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
