"""Lossless, content-addressed image objects; no retention or PV operations."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import numpy as np


MAX_IMAGE_BYTES = 128 * 1024**2


def array_digest(array):
    array = np.asarray(array)
    if array.dtype.kind not in "uifSU" or array.nbytes > MAX_IMAGE_BYTES or array.size == 0:
        raise ValueError("Image must be a nonempty numeric/fixed-string array below 128 MiB")
    header = json.dumps([array.dtype.str, list(array.shape)], separators=(",", ":")).encode()
    hasher = hashlib.sha256(header + b"\n")
    hasher.update(np.ascontiguousarray(array).tobytes())
    return hasher.hexdigest()


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_array(path):
    """Read numeric .npy or our one-array .npz; never enable pickle."""
    path = Path(path)
    if path.suffix == ".npz":
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) != 1 or members[0].filename != "image.npy" or members[0].file_size > MAX_IMAGE_BYTES + 65536:
                    raise ValueError("Expected one bounded image.npy array in NPZ")
        except zipfile.BadZipFile as exc:
            raise ValueError("Corrupt image NPZ object") from exc
        with np.load(path, allow_pickle=False) as archive:
            array = archive["image"]
    elif path.suffix == ".npy":
        array = np.load(path, allow_pickle=False, mmap_mode="r")
    else:
        raise ValueError("Use a numeric .npy or .npz background, not a display image")
    array_digest(array)
    return np.array(array, copy=True)


def store_usage(root):
    root = Path(root)
    objects = [*root.glob("*.npz"), *root.glob(".image_*.tmp")] if root.is_dir() else []
    used = sum(path.stat().st_size for path in objects)
    existing = root.resolve()
    while not existing.exists():
        existing = existing.parent
    return {"used_bytes": used, "free_bytes": shutil.disk_usage(existing).free}


class ImageStore:
    def __init__(self, root, limit_bytes):
        self.root = Path(root).resolve()
        self.limit_bytes = int(limit_bytes)
        if self.limit_bytes <= 0:
            raise ValueError("Image storage limit must be positive")

    def put(self, array):
        array = np.asarray(array)
        key = array_digest(array)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / (key + ".npz")
        # Serialize writers sharing this pool, including the quota check.
        with (self.root / ".writer.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not destination.exists():
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.root, prefix=".image_", suffix=".tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                        np.savez_compressed(stream, image=array)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if store_usage(self.root)["used_bytes"] > self.limit_bytes:
                        raise OSError("Image storage limit reached; no images were deleted")
                    temporary.replace(destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            loaded = load_array(destination)
            if array_digest(loaded) != key:
                raise ValueError("Existing image object is corrupt; it was not overwritten")
        return {"sha256": key, "file_sha256": file_hash(destination), "dtype": array.dtype.str,
                "shape": list(array.shape), "raw_bytes": array.nbytes, "stored_bytes": destination.stat().st_size}

    def get(self, reference):
        key = reference["sha256"]
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid image object identity")
        path = self.root / (key + ".npz")
        if file_hash(path) != reference["file_sha256"]:
            raise ValueError("Image object checksum mismatch")
        array = load_array(path)
        if (array_digest(array) != key or array.dtype.str != reference["dtype"]
                or list(array.shape) != reference["shape"] or array.nbytes != reference["raw_bytes"]
                or path.stat().st_size != reference["stored_bytes"]):
            raise ValueError("Image object metadata mismatch")
        return array
