# platform -- the build target's identity, as CPython's `platform` reports
# the machine it runs on. A cross build answers for its target.
#
# Gaps vs. CPython: only `system()` and `machine()`; `node()`, `release()`,
# `version()`, `uname()`, `processor()`, `python_implementation()` and the
# rest are not implemented.
__tpy__ = {"cpp_namespace": "tpystd::platform",
           "includes": ["<tpy/platform.hpp>", "<tpy/platform_machine.hpp>"]}
import sys
from typing import Final
from tpy.extern import native_global

_MACHINE: Final[str] = native_global("tpy::platform_machine")


def system() -> str:
    """The OS name: 'Linux', 'Darwin', 'FreeBSD' or 'Windows'."""
    # A local copy: `match sys.platform` itself is BUGS.md#module-str-constant-in-match-rejects.
    plat = sys.platform
    match plat:
        case "linux":
            return "Linux"
        case "darwin":
            return "Darwin"
        case "win32":
            return "Windows"
        case _ if plat.startswith("freebsd"):
            return "FreeBSD"
        case _:
            return ""


def machine() -> str:
    """The machine type, as `os.uname().machine` spells it on the target
    ('x86_64', 'aarch64'; 'arm64' on macOS)."""
    return _MACHINE
