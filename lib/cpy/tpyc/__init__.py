"""CPython stub for the tpyc compiler package.

This file is on sys.path when user code runs under CPython via
`PYTHONPATH=lib/cpy:...`, and it *shadows* the real tpyc package. We
therefore can't `import tpyc` to get at the real values; instead we read
the installed distribution's metadata (works for both editable and
wheel installs of tpy-lang).

Exposed: __version__, VERSION_INFO -- mirroring the real tpyc package so
that lib/cpy/tpy/version.py (and any future cpy-side consumer) can
`from tpyc import __version__, VERSION_INFO` the same way it would
under the compiled path.
"""

import re
from importlib.metadata import PackageNotFoundError, version as _dist_version


def _parse_version_info(v: str) -> tuple[int, int, int, str, int]:
    """Parse a PEP 440 version string into a CPython-style version_info tuple.

    Mirrors tpyc.__init__._parse_version_info (kept in sync).
    Raises ValueError on malformed input.
    """
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


try:
    __version__: str = _dist_version("tpy-lang")
except PackageNotFoundError as e:
    raise PackageNotFoundError(
        "tpy-lang must be installed (e.g. `uv sync` or `pip install -e .`) "
        "for tpy.version to work under CPython -- the cpy-side tpyc stub "
        "reads the version from installed distribution metadata."
    ) from e

VERSION_INFO: tuple[int, int, int, str, int] = _parse_version_info(__version__)
