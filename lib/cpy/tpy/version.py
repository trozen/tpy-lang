"""CPython stub: version/implementation identification for TPy.

Mirrors lib/tpy/tpy/version.py. Reads canonical values from the tpyc
stub (lib/cpy/tpyc/__init__.py), which in turn pulls them from the
installed distribution metadata -- so there's a single source of truth
(tpyc/__init__.py -> dist-info -> here).

    from tpy.version import __version__, version_info, is_compiled
"""
from tpy import Int32
from tpyc import __version__ as _tpyc_version, VERSION_INFO as _tpyc_version_info

__version__: str = _tpyc_version
version_info: tuple[Int32, Int32, Int32, str, Int32] = _tpyc_version_info
is_compiled: bool = False
