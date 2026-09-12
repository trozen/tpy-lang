"""Compiler/runtime version and implementation identification for TPy.

Usage:

    from tpy.version import __version__, version_info, is_compiled

Lives as a dedicated submodule (not re-exported from tpy/__init__.py)
because variable re-exports through native_module facades and transitive
init-chain propagation through them are not currently supported. Values
come from the _version.py macros, which read tpyc.__version__ and
tpyc.VERSION_INFO at compile time.
"""
from typing import Final
from tpy import int32
from ._version import version as _version, version_info as _version_info

__version__: Final[str] = _version()
# int32 components rather than `int` (BigInt): version numbers are small
# and BigInt would waste heap allocations on every access.
version_info: Final[tuple[int32, int32, int32, str, int32]] = _version_info()
is_compiled: Final[bool] = True
