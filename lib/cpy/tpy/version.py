"""CPython stub: version/implementation identification for TPy.

Mirrors lib/tpy/tpy/version.py. `tpy` ships in the tpy-lang distribution next
to the compiler, so the version comes from the real tpyc package:

    from tpy.version import __version__, version_info, is_compiled
"""
from tpy import int32
from tpyc import __version__ as _tpyc_version, VERSION_INFO as _tpyc_version_info

__version__: str = _tpyc_version
version_info: tuple[int32, int32, int32, str, int32] = _tpyc_version_info
is_compiled: bool = False
