"""SSH + SFTP helpers for per-category reference embedding capture on the Pi."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.services.pi_ssh import expand_pi_path, pi_home, resolved_host, ssh_client
from config import (
    PI_REFERENCE_CAPTURE_SCRIPT_PATH,
    PI_REFERENCE_CAPTURE_WAIT_SEC,
    PI_REFERENCE_LIBRARY_DIR,
    PI_VIT_VENV,
    VIT_REFERENCE_EMBEDDINGS_FILE,
    VIT_REFERENCE_LIBRARY_DIR,
)

CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,48}$")
CACHE_FILENAME = "cache_embeddings.json"

_last_capture: dict | None = None
_active_category: str | None = None


class ReferenceCaptureError(Exception):
    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def sanitize_category(category: str) -> str:
    slug = category.strip().lower()
    if not CATEGORY_RE.match(slug):
        raise ReferenceCaptureError(
            f"invalid category slug: {category!r} "
            "(use 1-48 chars: a-z, 0-9, _, -)"
        )
    return slug


def _pi_library_dir() -> str:
    return expand_pi_path(PI_REFERENCE_LIBRARY_DIR, pi_home())


def _pi_category_json(category: str) -> str:
    return f"{_pi_library_dir()}/{category}/{CACHE_FILENAME}"


def _local_library_dir() -> Path:
    return Path(VIT_REFERENCE_LIBRARY_DIR)


def _local_category_dir(category: str) -> Path:
    return _local_library_dir() / category


def _local_category_json(category: str) -> Path:
    return _local_category_dir(category) / CACHE_FILENAME


def _pi_script_path() -> str:
    home = pi_home()
    script = expand_pi_path(PI_REFERENCE_CAPTURE_SCRIPT_PATH, home)
    if not script.startswith("/"):
        script = f"{home}/{script}"
    return script


def _count_objects(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        objects = data.get("objects", []) if isinstance(data, dict) else []
        return len([obj for obj in objects if isinstance(obj, dict)])
    except Exception:
        return 0


def _parse_capture_stdout(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    raise ReferenceCaptureError(
        "capture script did not return JSON result",
        details={"stdout": stdout[-2000:]},
    )


def _remote_exec(command: str, *, timeout: float) -> tuple[int, str, str]:
    host = resolved_host()
    client = ssh_client(host)
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return exit_code, out, err
    finally:
        client.close()


def _list_remote_categories() -> list[str]:
    lib = shlex.quote(_pi_library_dir())
    cmd = f"test -d {lib} && ls -1 {lib} 2>/dev/null || true"
    try:
        _, out, _ = _remote_exec(cmd, timeout=8)
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        name = line.strip().lower()
        if CATEGORY_RE.match(name):
            names.append(name)
    return names


def _list_local_categories() -> list[str]:
    root = _local_library_dir()
    if not root.exists():
        return []
    names = []
    for child in root.iterdir():
        if child.is_dir() and CATEGORY_RE.match(child.name):
            names.append(child.name)
    return names


def list_categories() -> list[dict]:
    seen: dict[str, dict] = {}
    for name in sorted(set(_list_local_categories()) | set(_list_remote_categories())):
        local_path = _local_category_json(name)
        seen[name] = {
            "category": name,
            "snapshot_count": _count_objects(local_path),
            "local_path": str(local_path),
            "pi_path": _pi_category_json(name),
            "active": name == _active_category,
        }
    return list(seen.values())


def get_category_meta(category: str) -> dict:
    slug = sanitize_category(category)
    local_path = _local_category_json(slug)
    mtime = None
    if local_path.exists():
        mtime = datetime.fromtimestamp(
            local_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
    return {
        "category": slug,
        "snapshot_count": _count_objects(local_path),
        "local_path": str(local_path),
        "pi_path": _pi_category_json(slug),
        "modified_at": mtime,
        "active": slug == _active_category,
    }


def sync_category(category: str) -> dict:
    slug = sanitize_category(category)
    host = resolved_host()
    remote_path = _pi_category_json(slug)
    local_path = _local_category_json(slug)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    client = ssh_client(host)
    try:
        sftp = client.open_sftp()
        try:
            sftp.get(remote_path, str(local_path))
        except FileNotFoundError as exc:
            raise ReferenceCaptureError(
                f"remote reference file not found: {remote_path}",
                details={"category": slug, "host": host},
            ) from exc
        finally:
            sftp.close()
    finally:
        client.close()

    return {
        "status": "ok",
        "category": slug,
        "synced": True,
        "snapshot_count": _count_objects(local_path),
        "local_path": str(local_path),
        "pi_path": remote_path,
    }


def capture_snapshot(category: str, *, label: str = "bottle", sync: bool = True) -> dict:
    global _last_capture

    slug = sanitize_category(category)
    script = _pi_script_path()
    workdir = str(PurePosixPath(script).parent) or pi_home()
    output_dir = _pi_library_dir()
    wait_sec = PI_REFERENCE_CAPTURE_WAIT_SEC

    venv = expand_pi_path(PI_VIT_VENV, pi_home())
    cmd = (
        f"cd {shlex.quote(workdir)} && "
        f"source {shlex.quote(venv)} && "
        f"python3 {shlex.quote(script)} "
        f"--category {shlex.quote(slug)} "
        f"--output-dir {shlex.quote(output_dir)} "
        f"--label {shlex.quote(label)} "
        f"--wait-sec {wait_sec}"
    )

    try:
        exit_code, stdout, stderr = _remote_exec(cmd, timeout=wait_sec + 15)
    except Exception as exc:
        raise ReferenceCaptureError(f"SSH capture failed: {exc}") from exc

    if exit_code != 0:
        raise ReferenceCaptureError(
            "capture script failed on Pi",
            details={"exit_code": exit_code, "stdout": stdout[-2000:], "stderr": stderr[-2000:]},
        )

    result = _parse_capture_stdout(stdout)
    if result.get("status") != "ok":
        raise ReferenceCaptureError(
            result.get("message", "capture script returned error"),
            details=result,
        )

    payload = {
        "status": "ok",
        "category": slug,
        "sample_id": result.get("sample_id"),
        "total": result.get("total"),
        "label": label,
        "pi_path": result.get("path", _pi_category_json(slug)),
        "synced": False,
    }

    if sync:
        sync_result = sync_category(slug)
        payload.update(sync_result)
        payload["synced"] = True

    _last_capture = payload
    return payload


def activate_category(category: str) -> dict:
    global _active_category

    slug = sanitize_category(category)
    source = _local_category_json(slug)
    if not source.exists():
        raise ReferenceCaptureError(
            f"category not synced locally: {slug}",
            details={"local_path": str(source)},
        )

    target = Path(VIT_REFERENCE_EMBEDDINGS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    _active_category = slug

    return {
        "status": "ok",
        "category": slug,
        "active": True,
        "snapshot_count": _count_objects(source),
        "reference_path": str(target),
        "source_path": str(source),
    }


def get_reference_capture_status() -> dict:
    active = _active_category
    local_path = _local_category_json(active) if active else None
    return {
        "active_category": active,
        "snapshot_count": _count_objects(local_path) if local_path else 0,
        "library_dir": str(_local_library_dir()),
        "pi_library_dir": _pi_library_dir(),
        "last_capture": _last_capture,
        "categories": list_categories(),
    }


def get_active_category() -> str | None:
    return _active_category
