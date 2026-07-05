# tpy: cpp_namespace("tpystd::zoneinfo")
# The zoneinfo module: IANA time zones for datetime. The classes live in
# datetime.py (the ZoneInfo <-> datetime signature cycle forces one
# module); this facade is the CPython-spelled import surface plus
# available_timezones over the provider's zone database.
# Unsupported: ZoneInfo.no_cache / .clear_cache (permanent -- their whole
# observable effect is identity-distinct same-key instances and mid-run
# tz-db reload, both outside the value-typed pin-once model);
# ZoneInfo.from_file and TZPATH / reset_tzpath (deferred -- backend work,
# see the roadmap). All are loud absences.
from datetime import ZoneInfo, ZoneInfoNotFoundError
from tpy import Int32, Own
from _bindings import hinnant_date


def available_timezones() -> Own[set[str]]:
    # The provider's zone database, so every returned key constructs a
    # ZoneInfo. CPython's file walk additionally lists placeholder
    # entries (Factory, localtime) that are not real zones; the provider
    # skips them (declared divergence, see the roadmap).
    out: set[str] = set()
    n = int(hinnant_date.zone_db_count())
    for i in range(n):
        out.add(hinnant_date.zone_db_key_at(Int32(i)))
    return out
