# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::tz_intern")
# tpy: include("<tpy/stdlib/tz_intern.hpp>")
"""Timezone-name intern table binding for the `datetime` stdlib module.

Header-only (no managed lib): deliberately separate from hinnant_date so
pure calendar code does not link the tz backend. Id 0 means "no name".

Not for direct user import; the public surface is `datetime`.
"""

from tpy import int32
from tpy.extern import native


@native("tpy::stdlib::tz_intern::intern_name")
def intern_name(name: str) -> int32: ...


@native("tpy::stdlib::tz_intern::name_at")
def name_at(id: int32) -> str: ...
