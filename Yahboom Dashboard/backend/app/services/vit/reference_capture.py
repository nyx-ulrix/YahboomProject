"""Local reference embedding capture from Pi MQTT embeddings relayed by vit_service."""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from app.services.vit.image_encoder import ImageEncoderError, image_encoder

from config import (
    EDGE_AWARE_REFERENCE_THRESHOLD,
    VIT_REFERENCE_DEFAULT_THRESHOLD,
    VIT_REFERENCE_EMBEDDINGS_FILE,
    VIT_REFERENCE_LABEL,
    VIT_REFERENCE_LIBRARY_DIR,
    VIT_STOP_REFERENCE_CATEGORY,
)

CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,48}$")
CACHE_FILENAME = "cache_embeddings.json"
ACTIVE_STATE_FILE = ".active.json"
MODEL_NAME = "MobileCLIP-S1"
MODEL_PRETRAINED = "datacompdr"
ALLOWED_EMBED_BYTES = (512, 1024, 2048)
# Reject captures when the relayed embedding is older than this (seconds).
MAX_EMBEDDING_AGE_SEC = 12.0

_last_capture: dict | None = None
_active_category: str | None = None
_active_embedding_size_bytes: int | None = None


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


def snap_embed_bytes(value: int) -> int:
    return min(ALLOWED_EMBED_BYTES, key=lambda n: abs(n - int(value)))


def _local_library_dir() -> Path:
    return Path(VIT_REFERENCE_LIBRARY_DIR)


def _active_state_path() -> Path:
    return _local_library_dir() / ACTIVE_STATE_FILE


def _local_category_dir(category: str) -> Path:
    return _local_library_dir() / category


def _legacy_category_json(category: str) -> Path:
    """Pre-size-folder layout: {category}/cache_embeddings.json (treated as 2048 B)."""
    return _local_category_dir(category) / CACHE_FILENAME


def _local_category_json(category: str, embedding_size_bytes: int) -> Path:
    size = snap_embed_bytes(embedding_size_bytes)
    return _local_category_dir(category) / str(size) / CACHE_FILENAME


def _resolve_embedding_size_bytes(embedding: dict) -> int:
    size = embedding.get("embedding_size")
    if isinstance(size, int) and size in ALLOWED_EMBED_BYTES:
        return size
    dim = embedding.get("embedding_dim")
    if isinstance(dim, int) and dim > 0:
        return snap_embed_bytes(dim * 4)
    raise ReferenceCaptureError(
        "could not determine embedding size from relayed payload",
        details={"embedding_keys": sorted(embedding.keys())},
    )


def _json_path_for_size(category: str, embedding_size_bytes: int) -> Path | None:
    """Sized library file, with legacy 2048 B fallback at category root."""
    size = snap_embed_bytes(embedding_size_bytes)
    sized = _local_category_json(category, size)
    if sized.exists():
        return sized
    if size == 2048:
        legacy = _legacy_category_json(category)
        if legacy.exists():
            return legacy
    return None


def _count_objects(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        objects = data.get("objects", []) if isinstance(data, dict) else []
        return len([obj for obj in objects if isinstance(obj, dict)])
    except Exception:
        return 0


def _list_local_categories() -> list[str]:
    root = _local_library_dir()
    if not root.exists():
        return []
    names: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or not CATEGORY_RE.match(child.name):
            continue
        names.add(child.name)
        if (child / CACHE_FILENAME).exists():
            names.add(child.name)
        for sub in child.iterdir():
            if sub.is_dir() and sub.name.isdigit() and (sub / CACHE_FILENAME).exists():
                names.add(child.name)
    return sorted(names)


def _list_sizes_for_category(category: str) -> list[int]:
    cat_dir = _local_category_dir(category)
    if not cat_dir.exists():
        return []
    sizes: set[int] = set()
    for child in cat_dir.iterdir():
        if child.is_dir() and child.name.isdigit():
            nbytes = int(child.name)
            if nbytes in ALLOWED_EMBED_BYTES and (child / CACHE_FILENAME).exists():
                sizes.add(nbytes)
    if _legacy_category_json(category).exists():
        sizes.add(2048)
    return sorted(sizes)


def _load_active_state() -> None:
    global _active_category, _active_embedding_size_bytes
    path = _active_state_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cat = data.get("category")
        size = data.get("embedding_size_bytes")
        if isinstance(cat, str) and CATEGORY_RE.match(cat):
            _active_category = cat
        if isinstance(size, int):
            _active_embedding_size_bytes = snap_embed_bytes(size)
    except Exception:
        pass


def _save_active_state() -> None:
    if _active_category is None or _active_embedding_size_bytes is None:
        return
    root = _local_library_dir()
    root.mkdir(parents=True, exist_ok=True)
    _active_state_path().write_text(
        json.dumps({
            "category": _active_category,
            "embedding_size_bytes": _active_embedding_size_bytes,
        }, indent=2),
        encoding="utf-8",
    )


_load_active_state()


def _parse_iso_age_sec(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        return time.time() - ts
    except (TypeError, ValueError):
        return None


def _load_objects(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        objects = data.get("objects", []) if isinstance(data, dict) else []
        return [obj for obj in objects if isinstance(obj, dict)]
    except Exception:
        return []


def _next_sample_id(objects: list[dict]) -> int:
    ids = [int(obj["sample_id"]) for obj in objects if obj.get("sample_id") is not None]
    return (max(ids) if ids else 0) + 1


def _write_cache_atomic(json_path: Path, objects: list[dict]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"objects": objects}, indent=2), encoding="utf-8")
    tmp_path.replace(json_path)


def list_categories() -> list[dict]:
    entries = []
    for name in _list_local_categories():
        sizes = _list_sizes_for_category(name)
        size_entries = [
            {
                "embedding_size_bytes": size,
                "snapshot_count": _count_objects(_json_path_for_size(name, size)),
                "local_path": str(_json_path_for_size(name, size) or _local_category_json(name, size)),
            }
            for size in sizes
        ]
        total = sum(s["snapshot_count"] for s in size_entries)
        entries.append({
            "category": name,
            "snapshot_count": total,
            "sizes": size_entries,
            "active": name == _active_category,
            "active_embedding_size_bytes": (
                _active_embedding_size_bytes
                if name == _active_category
                else None
            ),
        })
    return entries


def get_category_meta(category: str) -> dict:
    slug = sanitize_category(category)
    sizes = _list_sizes_for_category(slug)
    return {
        "category": slug,
        "sizes": sizes,
        "snapshot_count": sum(
            _count_objects(_json_path_for_size(slug, size)) for size in sizes
        ),
        "active": slug == _active_category,
        "active_embedding_size_bytes": (
            _active_embedding_size_bytes if slug == _active_category else None
        ),
    }


def name_to_category(name: str) -> str:
    """Turn a display name like 'tea canister' into a library slug."""
    slug = name.strip().lower().replace(" ", "_")
    slug = re.sub(r"[^a-z0-9_-]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return sanitize_category(slug or "reference")


def _images_dir(category: str, embedding_size_bytes: int) -> Path:
    size = snap_embed_bytes(embedding_size_bytes)
    return _local_category_dir(category) / str(size) / "images"


def _safe_image_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("._")
    return cleaned[:120] or "upload.png"


def _save_sample_image(
    category: str,
    embedding_size_bytes: int,
    sample_id: int,
    image_bytes: bytes,
    original_filename: str,
) -> str:
    images_dir = _images_dir(category, embedding_size_bytes)
    images_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_image_filename(original_filename)
    dest = images_dir / f"{sample_id}_{safe_name}"
    dest.write_bytes(image_bytes)
    return dest.name


def _find_sample_image(
    category: str,
    embedding_size_bytes: int,
    sample_id: int,
    image_file: str | None = None,
) -> Path | None:
    images_dir = _images_dir(category, embedding_size_bytes)
    if image_file:
        candidate = images_dir / Path(image_file).name
        if candidate.exists():
            return candidate
    if not images_dir.exists():
        return None
    prefix = f"{sample_id}_"
    for child in sorted(images_dir.iterdir()):
        if child.is_file() and child.name.startswith(prefix):
            return child
    return None


def _append_library_object(
    slug: str,
    embedding_size_bytes: int,
    obj: dict,
) -> tuple[Path, int, int]:
    json_path = _local_category_json(slug, embedding_size_bytes)
    objects = _load_objects(json_path)
    sample_id = _next_sample_id(objects)
    obj["sample_id"] = sample_id
    objects.append(obj)
    _write_cache_atomic(json_path, objects)
    return json_path, sample_id, len(objects)


def capture_snapshot_from_image(
    category: str,
    image_bytes: bytes,
    *,
    label: str,
    original_filename: str = "upload.png",
    embedding_size_bytes: int = 2048,
) -> dict:
    """Encode a static image and append it to the reference library."""
    global _last_capture

    slug = sanitize_category(category)
    display_label = label.strip() or slug.replace("_", " ")
    data_b64, embedding_dim, size = image_encoder.encode_image_bytes(
        image_bytes,
        embedding_size_bytes=embedding_size_bytes,
    )

    obj: dict = {
        "label": display_label,
        "model": MODEL_NAME,
        "pretrained": MODEL_PRETRAINED,
        "embedding_dim": embedding_dim,
        "embedding_size_bytes": size,
        "threshold": VIT_REFERENCE_DEFAULT_THRESHOLD,
        "normalised": True,
        "dtype": "float32",
        "source": "dashboard_image_upload",
        "data": data_b64,
        "created_at": time.time(),
    }

    json_path, sample_id, total = _append_library_object(slug, size, obj)
    image_name = _save_sample_image(slug, size, sample_id, image_bytes, original_filename)
    obj["sample_id"] = sample_id
    obj["image_file"] = image_name

    # Rewrite with image_file metadata.
    objects = _load_objects(json_path)
    for entry in objects:
        if entry.get("sample_id") == sample_id:
            entry["image_file"] = image_name
            break
    _write_cache_atomic(json_path, objects)

    payload = {
        "status": "ok",
        "category": slug,
        "embedding_size_bytes": size,
        "sample_id": sample_id,
        "total": total,
        "label": display_label,
        "embedding_dim": embedding_dim,
        "local_path": str(json_path),
        "image_file": image_name,
    }
    _last_capture = payload
    return payload


def list_library_samples(embedding_size_bytes: int | None = None) -> list[dict]:
    """All samples across categories with optional preview metadata."""
    if embedding_size_bytes is None:
        embedding_size_bytes = _active_embedding_size_bytes or 2048
    size = snap_embed_bytes(embedding_size_bytes)

    samples: list[dict] = []
    for name in _list_local_categories():
        path = _json_path_for_size(name, size)
        if path is None:
            continue
        for obj in _load_objects(path):
            sample_id = obj.get("sample_id")
            if sample_id is None:
                continue
            image_file = obj.get("image_file")
            has_image = _find_sample_image(name, size, int(sample_id), image_file) is not None
            samples.append({
                "category": name,
                "sample_id": int(sample_id),
                "label": obj.get("label", VIT_REFERENCE_LABEL),
                "source": obj.get("source"),
                "embedding_size_bytes": size,
                "created_at": obj.get("created_at"),
                "image_file": image_file,
                "has_image": has_image,
            })
    samples.sort(key=lambda s: (s["category"], s["sample_id"]))
    return samples


def move_library_sample(
    from_category: str,
    sample_id: int,
    to_category: str,
    *,
    label: str | None = None,
    embedding_size_bytes: int | None = None,
) -> dict:
    """Move one sample (and its preview image) to another reference category."""
    src_slug = sanitize_category(from_category)
    dst_slug = sanitize_category(to_category)
    if src_slug == dst_slug:
        raise ReferenceCaptureError("source and destination category are the same")

    if embedding_size_bytes is None:
        embedding_size_bytes = _active_embedding_size_bytes or 2048
    size = snap_embed_bytes(embedding_size_bytes)

    src_path = _json_path_for_size(src_slug, size)
    if src_path is None:
        raise ReferenceCaptureError(
            f"no samples in {src_slug} at {size} B",
            details={"category": src_slug, "embedding_size_bytes": size},
        )

    objects = _load_objects(src_path)
    moved: dict | None = None
    remaining: list[dict] = []
    for obj in objects:
        if moved is None and obj.get("sample_id") == sample_id:
            moved = dict(obj)
        else:
            remaining.append(obj)

    if moved is None:
        raise ReferenceCaptureError(
            f"sample {sample_id} not found in {src_slug}",
            details={"category": src_slug, "sample_id": sample_id},
        )

    _write_cache_atomic(src_path, remaining)

    image_bytes: bytes | None = None
    image_file = moved.get("image_file")
    src_image = _find_sample_image(src_slug, size, sample_id, image_file)
    if src_image and src_image.exists():
        image_bytes = src_image.read_bytes()
        src_image.unlink(missing_ok=True)

    moved.pop("sample_id", None)
    if label is not None and label.strip():
        moved["label"] = label.strip()
    moved["source"] = "dashboard_moved"
    moved["moved_at"] = time.time()
    moved["moved_from"] = src_slug

    dst_path, new_id, total = _append_library_object(dst_slug, size, moved)

    if image_bytes is not None:
        new_image = _save_sample_image(
            dst_slug,
            size,
            new_id,
            image_bytes,
            str(image_file or f"{sample_id}.png"),
        )
        dst_objects = _load_objects(dst_path)
        for entry in dst_objects:
            if entry.get("sample_id") == new_id:
                entry["image_file"] = new_image
                break
        _write_cache_atomic(dst_path, dst_objects)
    else:
        new_image = None

    return {
        "status": "ok",
        "from_category": src_slug,
        "to_category": dst_slug,
        "from_sample_id": sample_id,
        "sample_id": new_id,
        "total": total,
        "label": moved.get("label"),
        "embedding_size_bytes": size,
        "image_file": new_image,
        "source_path": str(src_path),
        "destination_path": str(dst_path),
    }


def get_sample_image_path(
    category: str,
    sample_id: int,
    embedding_size_bytes: int | None = None,
) -> Path | None:
    slug = sanitize_category(category)
    if embedding_size_bytes is None:
        embedding_size_bytes = _active_embedding_size_bytes or 2048
    size = snap_embed_bytes(embedding_size_bytes)
    path = _json_path_for_size(slug, size)
    if path is None:
        return None
    for obj in _load_objects(path):
        if obj.get("sample_id") == sample_id:
            return _find_sample_image(slug, size, sample_id, obj.get("image_file"))
    return None


def capture_snapshot_from_relay(
    category: str,
    embedding: dict,
    *,
    label: str = VIT_REFERENCE_LABEL,
    expected_seq: int | None = None,
) -> dict:
    """Append one relayed Pi embedding to {category}/{embedding_size_bytes}/."""
    global _last_capture

    slug = sanitize_category(category)
    data_b64 = embedding.get("data")
    if not data_b64:
        raise ReferenceCaptureError(
            "no Pi embedding available — start VIT.py on the Pi and wait for MQTT"
        )

    seq = embedding.get("seq")
    if expected_seq is not None and seq is not None and int(expected_seq) != int(seq):
        raise ReferenceCaptureError(
            f"embedding seq mismatch (got {seq}, expected {expected_seq}) — retry capture",
            details={"seq": seq, "expected_seq": expected_seq},
        )

    age = _parse_iso_age_sec(embedding.get("timestamp"))
    if age is not None and age > MAX_EMBEDDING_AGE_SEC:
        raise ReferenceCaptureError(
            f"Pi embedding is stale ({age:.1f}s old) — point the camera and retry",
            details={"age_sec": age},
        )

    embedding_size_bytes = _resolve_embedding_size_bytes(embedding)
    embedding_dim = embedding.get("embedding_dim")
    if embedding_dim is None:
        embedding_dim = embedding_size_bytes // 4

    json_path = _local_category_json(slug, embedding_size_bytes)
    objects = _load_objects(json_path)
    sample_id = _next_sample_id(objects)

    obj: dict = {
        "label": label,
        "sample_id": sample_id,
        "model": MODEL_NAME,
        "pretrained": MODEL_PRETRAINED,
        "embedding_dim": embedding_dim,
        "embedding_size_bytes": embedding_size_bytes,
        "threshold": VIT_REFERENCE_DEFAULT_THRESHOLD,
        "normalised": True,
        "dtype": "float32",
        "source": "pi_mqtt_relay",
        "frame_id": embedding.get("frame_id"),
        "data": data_b64,
        "created_at": time.time(),
        "relay_seq": seq,
        "relay_timestamp": embedding.get("timestamp"),
    }
    if embedding.get("image_file_size") is not None:
        obj["image_file_size"] = embedding.get("image_file_size")

    objects.append(obj)
    _write_cache_atomic(json_path, objects)

    payload = {
        "status": "ok",
        "category": slug,
        "embedding_size_bytes": embedding_size_bytes,
        "sample_id": sample_id,
        "total": len(objects),
        "label": label,
        "embedding_dim": embedding_dim,
        "frame_id": embedding.get("frame_id"),
        "local_path": str(json_path),
        "relay_seq": seq,
    }
    _last_capture = payload
    return payload


def activate_category(
    category: str,
    embedding_size_bytes: int | None = None,
) -> dict:
    global _active_category, _active_embedding_size_bytes

    slug = sanitize_category(category)
    if embedding_size_bytes is None:
        if _active_category == slug and _active_embedding_size_bytes is not None:
            size = _active_embedding_size_bytes
        else:
            sizes = _list_sizes_for_category(slug)
            if not sizes:
                raise ReferenceCaptureError(
                    f"category not captured yet: {slug}",
                    details={"category": slug},
                )
            size = sizes[-1]
    else:
        size = snap_embed_bytes(embedding_size_bytes)

    source = _json_path_for_size(slug, size)
    if source is None:
        raise ReferenceCaptureError(
            f"no reference library for {slug} at {size} B — capture at this embedding size first",
            details={
                "category": slug,
                "embedding_size_bytes": size,
                "available_sizes": _list_sizes_for_category(slug),
            },
        )

    target = Path(VIT_REFERENCE_EMBEDDINGS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    _active_category = slug
    _active_embedding_size_bytes = size
    _save_active_state()

    return {
        "status": "ok",
        "category": slug,
        "embedding_size_bytes": size,
        "active": True,
        "snapshot_count": _count_objects(source),
        "reference_path": str(target),
        "source_path": str(source),
        "available_sizes": _list_sizes_for_category(slug),
    }


def _clean_library_object(obj: dict, *, category: str) -> dict | None:
    entry = {
        "category": category,
        "sample_id": obj.get("sample_id"),
        "label": obj.get("label", VIT_REFERENCE_LABEL),
        "embedding_dim": obj.get("embedding_dim"),
        "threshold": obj.get("threshold", VIT_REFERENCE_DEFAULT_THRESHOLD),
    }
    if "data" in obj:
        entry["data"] = obj["data"]
    elif "embedding" in obj:
        entry["embedding"] = obj["embedding"]
    else:
        return None
    return entry


def load_library_for_client(embedding_size_bytes: int | None = None) -> dict:
    """All reference objects across every category at the given embedding size."""
    if embedding_size_bytes is None:
        embedding_size_bytes = _active_embedding_size_bytes or 2048
    size = snap_embed_bytes(embedding_size_bytes)

    cleaned: list[dict] = []
    categories: list[dict] = []
    stop_count = 0
    for name in _list_local_categories():
        path = _json_path_for_size(name, size)
        if path is None:
            continue
        objs = _load_objects(path)
        cat_count = 0
        for obj in objs:
            entry = _clean_library_object(obj, category=name)
            if entry is None:
                continue
            cleaned.append(entry)
            cat_count += 1
            if name == VIT_STOP_REFERENCE_CATEGORY:
                stop_count += 1
        if cat_count > 0:
            categories.append({
                "category": name,
                "snapshot_count": cat_count,
                "embedding_size_bytes": size,
            })

    return {
        "status": "ok",
        "embedding_size_bytes": size,
        "stop_category": VIT_STOP_REFERENCE_CATEGORY,
        "default_threshold": VIT_REFERENCE_DEFAULT_THRESHOLD,
        "stop_threshold": EDGE_AWARE_REFERENCE_THRESHOLD,
        "objects": cleaned,
        "count": len(cleaned),
        "categories": categories,
        "stop_category_count": stop_count,
    }


def get_library_client_status(embedding_size_bytes: int | None = None) -> dict:
    payload = load_library_for_client(embedding_size_bytes)
    return {
        "library_count": payload["count"],
        "stop_category_count": payload["stop_category_count"],
        "stop_category": payload["stop_category"],
        "embedding_size_bytes": payload["embedding_size_bytes"],
        "categories": payload["categories"],
    }


def get_reference_capture_status() -> dict:
    active = _active_category
    source = (
        _json_path_for_size(active, _active_embedding_size_bytes)
        if active and _active_embedding_size_bytes
        else None
    )
    return {
        "active_category": active,
        "active_embedding_size_bytes": _active_embedding_size_bytes,
        "snapshot_count": _count_objects(source) if source else 0,
        "library_dir": str(_local_library_dir()),
        "last_capture": _last_capture,
        "categories": list_categories(),
    }


def get_active_category() -> str | None:
    return _active_category


def get_active_embedding_size_bytes() -> int | None:
    return _active_embedding_size_bytes
