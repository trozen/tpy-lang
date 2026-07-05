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

from tpy import Int32, Int64
from tpy.extern import native


@native("tpy::stdlib::datetime::local_utc_offset_seconds")
def local_utc_offset_seconds(epoch_seconds: Int64) -> Int64: ...


@native("tpy::stdlib::datetime::local_zone_abbrev")
def local_zone_abbrev(epoch_seconds: Int64) -> str: ...


@native("tpy::stdlib::datetime::zone_lookup")
def zone_lookup(key: str) -> Int32: ...


@native("tpy::stdlib::datetime::zone_key")
def zone_key(zone_id: Int32) -> str: ...


@native("tpy::stdlib::datetime::zone_wall_offset_seconds")
def zone_wall_offset_seconds(zone_id: Int32, wall_seconds: Int64,
                             fold: Int32) -> Int64: ...


@native("tpy::stdlib::datetime::zone_wall_dst_seconds")
def zone_wall_dst_seconds(zone_id: Int32, wall_seconds: Int64,
                          fold: Int32) -> Int64: ...


@native("tpy::stdlib::datetime::zone_wall_abbrev")
def zone_wall_abbrev(zone_id: Int32, wall_seconds: Int64,
                     fold: Int32) -> str: ...


@native("tpy::stdlib::datetime::zone_utc_offset_seconds")
def zone_utc_offset_seconds(zone_id: Int32, epoch_seconds: Int64) -> Int64: ...


@native("tpy::stdlib::datetime::zone_db_count")
def zone_db_count() -> Int32: ...


@native("tpy::stdlib::datetime::zone_db_key_at")
def zone_db_key_at(index: Int32) -> str: ...
