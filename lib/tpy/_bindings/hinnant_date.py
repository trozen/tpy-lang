# tpy: native_module
# tpy: cpp_namespace("tpystd::_bindings::hinnant_date")
# tpy: include("<tpy/stdlib/datetime.hpp>")
# tpy: link("date", managed=True)
"""Timezone-backend binding for the `datetime` stdlib module.

One @native declaration per tpy/stdlib/datetime.hpp facade primitive.
The facade decouples the provider (currently the vendored Howard Hinnant
date library; see runtime/cpp/src/stdlib/date_shim.cpp) from TPy code,
so this surface must stay minimal and provider-neutral.

Not for direct user import; the public surface is `datetime`.
"""

from tpy import Int64
from tpy.extern import native


@native("tpy::stdlib::datetime::local_utc_offset_seconds")
def local_utc_offset_seconds(epoch_seconds: Int64) -> Int64: ...
