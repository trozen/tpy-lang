"""
TurboPython Compiler (tpyc)

A proof-of-concept compiler for TurboPython, a restricted Python-syntax
language that compiles to C++ for ultra-low-latency applications.
"""

import re
from pathlib import Path


def _resolve_data_dir(name: str) -> Path:
    """Find a data directory (runtime, lib) in dev or installed layout."""
    # Dev layout: tpyc/ is next to runtime/ and lib/
    dev_path = Path(__file__).parent.parent / name
    if dev_path.is_dir():
        return dev_path
    # Installed layout: hatchling puts them under tpyc/_data/
    return Path(__file__).parent / "_data" / name


def get_runtime_dir() -> Path:
    return _resolve_data_dir("runtime")


def get_lib_dir() -> Path:
    return _resolve_data_dir("lib")


def get_docs_dir() -> Path:
    return _resolve_data_dir("docs")


# Defined before sub-module imports so that macro_api (imported via sema)
# can re-export them without hitting a circular-import partial-init state.
__version__ = "0.6.1"

# Lives here (not compiler.py) so the CLI's argument parser can offer the
# choices without importing the compiler machinery.
DEFAULT_INT_CHOICES = ("int32", "int64", "BigInt")


def _parse_version_info(v: str) -> tuple[int, int, int, str, int]:
    """Parse a PEP 440 version string into a CPython-style version_info tuple.

    Shape: (major, minor, micro, releaselevel, serial).
    releaselevel: "alpha" | "beta" | "candidate" | "final" | "dev".

    Raises ValueError if the version string doesn't match one of the
    supported PEP 440 forms: X.Y.Z, X.Y.ZaN, X.Y.ZbN, X.Y.ZrcN, X.Y.Z.devN.
    """
    # Two alternate suffix shapes: a/b/rc attach directly (no dot);
    # .dev is preceded by a dot. Explicit alternation keeps each form
    # separate rather than smuggling the dot into the captured group.
    m = re.match(
        r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+)|\.dev(\d+))?$", v)
    if not m:
        raise ValueError(f"invalid PEP 440 version: {v!r}")
    major, minor, micro = int(m[1]), int(m[2]), int(m[3])
    if m[4] is not None:        # a/b/rc suffix
        level = {"a": "alpha", "b": "beta", "rc": "candidate"}[m[4]]
        serial = int(m[5])
    elif m[6] is not None:      # .dev suffix
        level, serial = "dev", int(m[6])
    else:
        level, serial = "final", 0
    return (major, minor, micro, level, serial)


VERSION_INFO: tuple[int, int, int, str, int] = _parse_version_info(__version__)


# Re-exports resolve lazily (PEP 562): pulling typesys/parse/sema/codegen
# eagerly costs ~250ms on EVERY `import tpyc.<anything>`, defeating the
# build cache's warm path (which must decide "binary is up to date, exec
# it" without loading the compiler at all).
_LAZY_EXPORTS = {
    "TpyType": "typesys", "VoidType": "typesys", "NominalType": "typesys",
    "PtrType": "typesys", "is_readonly_ptr": "typesys",
    "INT32": "typesys", "VOID": "typesys", "TypeRegistry": "typesys",
    "Parser": "parse", "ParseError": "parse", "TpyModule": "parse",
    "SemanticAnalyzer": "sema", "SemanticError": "sema",
    "CodeGenerator": "codegen_cpp",
}


def __getattr__(name: str):
    submodule = _LAZY_EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(f".{submodule}", __name__), name)
    globals()[name] = value
    return value


def get_git_commit() -> str:
    """Return git commit: from _buildinfo (installed) or live git (dev)."""
    try:
        from ._buildinfo import GIT_COMMIT
        return GIT_COMMIT
    except ImportError:
        pass
    import subprocess
    try:
        r = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"
__all__ = [
    "Parser", "ParseError",
    "SemanticAnalyzer", "SemanticError",
    "CodeGenerator",
    "TpyType", "VoidType", "NominalType",
    "PtrType", "is_readonly_ptr",
    "INT32", "VOID", "TypeRegistry", "TpyModule"
]
