"""Auto-updater backed by the GitHub releases API.

Philosophy: absolutely never silently replace the user's binary. We check
the latest release in the background, tell the user about it, and let them
kick off the actual install.

For AppImage installs we *can* do the download-and-swap end-to-end (the
kernel keeps the running mmap alive across an overwrite, so replacing the
file and re-execing works). For every other packaging (Windows Setup or
Portable, .deb, source) we just open the GitHub release page — the user
runs the installer themselves from there.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal


GITHUB_REPO = "Sin213/cove-video-editor"
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass
class UpdateInfo:
    latest_version: str
    release_url: str
    asset_name: str | None = None
    asset_url: str | None = None
    asset_size: int = 0


def _parse_version(v: str) -> tuple[int, int, int]:
    v = v.strip().lstrip("vV")
    out: list[int] = []
    for part in v.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
        if len(out) == 3:
            break
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


def version_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


class ChecksumError(RuntimeError):
    """Raised when the downloaded asset's sha256 does not match its sidecar.

    Surfaced as a typed failure so the download path can refuse to swap
    the running binary on a tampered or truncated download."""


def _parse_sha256_sidecar(text: str) -> str:
    """Pull the hex hash out of a ``sha256sum`` / ``Get-FileHash`` sidecar."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0]
        if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
            return token.lower()
        raise ChecksumError(f"unrecognized sidecar contents: {line!r}")
    raise ChecksumError("empty sidecar")


def _sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fetch_sha256_sidecar(url: str, timeout: float = 20.0) -> str:
    """GET the sidecar URL and return the parsed sha256 hex digest."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "cove-video-editor-updater"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Sidecar files are tiny; cap the read so a hostile redirect
        # can't dump unbounded bytes.
        raw = resp.read(4096).decode("ascii", errors="replace")
    return _parse_sha256_sidecar(raw)


def verify_sha256(path: Path, sidecar_url: str) -> None:
    """Verify ``path`` matches the hash advertised by ``sidecar_url``.

    On any failure (network error, malformed sidecar, hash mismatch) the
    partial download at ``path`` is unlinked and ChecksumError is raised
    so the caller never swaps in an unverified binary."""
    try:
        expected = fetch_sha256_sidecar(sidecar_url)
    except ChecksumError:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        path.unlink(missing_ok=True)
        raise ChecksumError(f"could not fetch sidecar: {exc}") from exc
    actual = _sha256_of_file(path)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise ChecksumError(
            f"sha256 mismatch: expected {expected}, got {actual}",
        )


def bundle_kind() -> str:
    """Rough detection of how this instance was packaged so we can pick the
    matching release asset."""
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if sys.platform == "win32":
        exe_path = Path(sys.executable).resolve()
        if not getattr(sys, "frozen", False):
            return "source"
        exe_str = str(exe_path)
        if "Program Files" in exe_str or r"AppData\Local" in exe_str:
            return "win-setup"
        return "win-portable"
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        return "deb"
    return "source"


def preferred_asset(kind: str, assets: list[dict]) -> dict | None:
    def first_match(predicate) -> dict | None:
        return next((a for a in assets if predicate(a["name"].lower())), None)

    if kind == "appimage":
        return first_match(lambda n: n.endswith(".appimage"))
    if kind == "deb":
        return first_match(lambda n: n.endswith(".deb"))
    if kind == "win-setup":
        return first_match(lambda n: "setup" in n and n.endswith(".exe"))
    if kind == "win-portable":
        return first_match(lambda n: "portable" in n and n.endswith(".exe"))
    return None


def fetch_latest_release(timeout: float = 8.0) -> dict | None:
    req = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "cove-video-editor-updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:  # noqa: BLE001
        return None


class UpdateCheckWorker(QObject):
    updateAvailable = Signal(object)   # UpdateInfo
    noUpdate = Signal()
    failed = Signal(str)

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._current = current_version

    def run(self) -> None:
        data = fetch_latest_release()
        if data is None:
            self.failed.emit("could not reach the releases API")
            return
        tag = data.get("tag_name") or ""
        if not tag:
            self.failed.emit("release had no tag_name")
            return
        latest = tag.lstrip("vV")
        if not version_newer(latest, self._current):
            self.noUpdate.emit()
            return
        assets = data.get("assets") or []
        asset = preferred_asset(bundle_kind(), assets)
        info = UpdateInfo(
            latest_version=latest,
            release_url=(
                data.get("html_url")
                or f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
            ),
            asset_name=asset["name"] if asset else None,
            asset_url=asset["browser_download_url"] if asset else None,
            asset_size=int(asset["size"]) if asset else 0,
        )
        self.updateAvailable.emit(info)


class DownloadWorker(QObject):
    """Stream a URL to a destination file, emitting progress as percentage."""

    progress = Signal(int)           # 0–100
    finished = Signal(str)           # destination path
    failed = Signal(str)

    def __init__(self, url: str, dest: Path) -> None:
        super().__init__()
        self._url = url
        self._dest = dest
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._url, headers={"User-Agent": "cove-video-editor-updater"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                written = 0
                self._dest.parent.mkdir(parents=True, exist_ok=True)
                with open(self._dest, "wb") as f:
                    while True:
                        if self._cancelled:
                            raise RuntimeError("cancelled")
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        if total > 0:
                            self.progress.emit(int(written * 100 / total))
            # The release pipeline publishes a `<asset>.sha256` sidecar
            # next to every artifact, so its download URL is the asset
            # URL plus the suffix. Verify before signalling success;
            # verify_sha256 unlinks the partial on any failure.
            verify_sha256(self._dest, self._url + ".sha256")
            self.finished.emit(str(self._dest))
        except Exception as exc:  # noqa: BLE001
            try:
                self._dest.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            self.failed.emit(str(exc))


def start_check(current_version: str) -> tuple[QThread, UpdateCheckWorker]:
    thread = QThread()
    worker = UpdateCheckWorker(current_version)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.updateAvailable.connect(thread.quit)
    worker.noUpdate.connect(thread.quit)
    worker.failed.connect(thread.quit)
    return thread, worker


def start_download(url: str, dest: Path) -> tuple[QThread, DownloadWorker]:
    thread = QThread()
    worker = DownloadWorker(url, dest)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    return thread, worker


def swap_in_appimage(new_path: Path) -> Path:
    """Install `new_path` next to the running AppImage under its own
    versioned filename, remove the old file, and return the new path.
    Caller is responsible for relaunching.

    Keeping the release asset's filename (instead of overwriting the old
    file in place) matches electron-updater semantics and keeps the
    on-disk name truthful - external launchers like Cove Nexus derive the
    installed version from it."""
    current = os.environ.get("APPIMAGE")
    if not current:
        raise RuntimeError("APPIMAGE env var not set - not an AppImage install")
    old = Path(current).resolve()
    target = old.parent / new_path.name
    tmp = target.with_name(target.name + ".part")
    shutil.move(str(new_path), str(tmp))
    mode = os.stat(tmp).st_mode
    os.chmod(tmp, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp, target)
    if target != old:
        try:
            old.unlink()  # unlinking the running file is fine on Linux
        except OSError:
            pass
    os.environ["APPIMAGE"] = str(target)
    return target


def relaunch(path: Path) -> None:
    """Spawn `path` detached from the current process, then return so the
    caller can quit the Qt app cleanly."""
    # start_new_session detaches from our process group so the child survives
    # our exit — the running process keeps the old binary mmap'd while the
    # new one takes over the path on disk.
    subprocess.Popen(
        [str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
