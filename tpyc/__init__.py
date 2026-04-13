"""
TurboPython Compiler (tpyc)

A proof-of-concept compiler for TurboPython, a restricted Python-syntax
language that compiles to C++ for ultra-low-latency applications.
"""

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


from .typesys import (
    TpyType, Int32Type, FixedIntType, VoidType, NamedType, PtrType, is_readonly_ptr,
    INT32, VOID, TypeRegistry
)
from .parse import Parser, ParseError, TpyModule
from .sema import SemanticAnalyzer, SemanticError
from .codegen_cpp import CodeGenerator

__version__ = "0.1.0.dev0"


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
    "TpyType", "Int32Type", "FixedIntType", "VoidType", "NamedType",
    "PtrType", "is_readonly_ptr",
    "INT32", "VOID", "TypeRegistry", "TpyModule"
]
