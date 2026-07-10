"""
Whole-run build cache for tpy/tpyc.

After a successful binary-producing build, the CLI writes a manifest next to
the binary recording every input that determined it: module sources, module
resolution outcomes (misses included), macro files and probes, the compiler's
own sources, runtime headers/sources, third-party sources, the resolved C++
toolchain, and the output-affecting CLI options. On the next run the manifest
is validated with stats (content hashes only where stats moved) and, when
everything matches, the whole pipeline is skipped and the recorded binary is
executed directly.

This module must stay cheap to import: the warm path runs before the compiler
machinery is imported (importing `tpyc.cli`'s full dependency tree costs
~250ms, an order of magnitude more than the check itself). Only stdlib +
`tpyc.modules.resolver` (os/dataclasses/pathlib) are allowed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .modules.resolver import ModuleResolver

SCHEMA_VERSION = 1
MANIFEST_NAME = "build-manifest.json"


def _hash_file(path: str) -> str | None:
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 16):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _hash_names(names: list[str]) -> str:
    h = hashlib.blake2b(digest_size=16)
    for name in sorted(names):
        h.update(name.encode())
        h.update(b"\0")
    return h.hexdigest()


def file_entry(path: str) -> dict | None:
    """Build a (size, mtime_ns, hash) manifest entry for one input file."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    digest = _hash_file(path)
    if digest is None:
        return None
    return {"path": path, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "hash": digest}


def _entry_fresh(entry: dict) -> bool:
    """Hybrid freshness check: trust the hash when (size, mtime) match,
    re-hash the content otherwise (ccache-style)."""
    try:
        st = os.stat(entry["path"])
    except OSError:
        return False
    if st.st_size == entry["size"] and st.st_mtime_ns == entry["mtime_ns"]:
        return True
    return st.st_size == entry["size"] and _hash_file(entry["path"]) == entry["hash"]


def dir_listing_entry(root: Path, pattern: str) -> dict:
    """Fingerprint of a directory's file-name set (relative names only).

    Catches inputs *added* to an enumerated set (a new runtime header, a
    new compiler source file) that per-file stats can't see.
    """
    names = [str(p.relative_to(root)) for p in root.rglob(pattern) if p.is_file()]
    return {"root": str(root), "pattern": pattern, "names_hash": _hash_names(names)}


@dataclass
class BuildManifest:
    """Everything that certifies `binary` as up-to-date."""
    options_key: dict
    toolchain: dict           # {"argv0", "resolved", "size", "mtime_ns"}
    files: list[dict]         # file_entry() dicts
    dir_listings: list[dict]  # dir_listing_entry() dicts
    resolver: dict            # {"base_dir", "extra_dirs", "extra_extensions"}
    resolutions: dict         # module name -> resolved path (or None on miss)
    must_not_exist: list[str]
    warnings: list[str]
    binary: dict              # file_entry() of the linked output
    schema: int = SCHEMA_VERSION


def toolchain_entry(argv0: str) -> dict | None:
    """Identity of the C++ compiler binary (resolved path + stat).

    Command-list equality is checked separately via the options key (the
    CLI re-runs CppCompilerConfig.from_env on the warm path, so a newly
    installed higher-versioned compiler that auto-detect would now prefer
    changes the key); this entry catches the resolved binary itself being
    replaced in place.
    """
    resolved = shutil.which(argv0)
    if resolved is None:
        return None
    try:
        st = os.stat(resolved)
    except OSError:
        return None
    return {"resolved": resolved, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def compute_build_dir(output_dir: Path, module_name: str, variant: str,
                      flat: bool) -> Path:
    """Variant build dir for an entry module.

    Mirrors BuildLayout's root/build-dir derivation (compiler.py) -- kept
    in lockstep by a unit test -- because importing BuildLayout here would
    pull the whole compiler into the warm path.
    """
    root = output_dir if flat else output_dir / f"{module_name}.d"
    return root / variant


def manifest_path(build_dir: Path) -> Path:
    return build_dir / MANIFEST_NAME


def write_manifest(build_dir: Path, manifest: BuildManifest) -> None:
    """Atomically persist the manifest (tmp + rename)."""
    target = manifest_path(build_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.__dict__, indent=1))
    os.replace(tmp, target)


@dataclass
class WarmHit:
    binary: str
    warnings: list[str] = field(default_factory=list)


def check_up_to_date(build_dir: Path, options_key: dict) -> WarmHit | None:
    """Validate the recorded manifest against the world; None means rebuild.

    Ordered cheapest-first; any mismatch (or any unreadable state) falls
    back to a full rebuild -- this function must never raise.
    """
    try:
        data = json.loads(manifest_path(build_dir).read_text())
    except (OSError, ValueError):
        return None
    try:
        if data.get("schema") != SCHEMA_VERSION:
            return None
        if data.get("options_key") != options_key:
            return None

        # Toolchain binary unchanged in place (command-list equality is part
        # of options_key -- the caller re-resolved it via from_env).
        tc = data["toolchain"]
        st = os.stat(tc["resolved"])
        if st.st_size != tc["size"] or st.st_mtime_ns != tc["mtime_ns"]:
            return None

        # Output binary present and untouched.
        if not _entry_fresh(data["binary"]):
            return None

        # Macro probe misses must still miss.
        for path in data["must_not_exist"]:
            if os.path.exists(path):
                return None

        # Replay module resolution through the real resolver: catches a
        # newly created file shadowing a recorded module (search order is
        # entry dir > -L dirs > lib/tpy) and any other resolution drift.
        res = data["resolver"]
        resolver = ModuleResolver(
            Path(res["base_dir"]),
            extra_dirs=[Path(d) for d in res["extra_dirs"]],
            extra_extensions=tuple(res["extra_extensions"]),
        )
        for name, recorded in data["resolutions"].items():
            now = resolver.resolve(name)
            if (str(now.path) if now else None) != recorded:
                return None

        # Enumerated input sets gained/lost no files.
        for listing in data["dir_listings"]:
            root = Path(listing["root"])
            if not root.is_dir():
                return None
            if dir_listing_entry(root, listing["pattern"])["names_hash"] \
                    != listing["names_hash"]:
                return None

        # Every recorded input file unchanged (stat fast path, hash fallback).
        for entry in data["files"]:
            if not _entry_fresh(entry):
                return None

        return WarmHit(binary=data["binary"]["path"],
                       warnings=list(data.get("warnings", [])))
    except (KeyError, TypeError, OSError):
        return None
