#pragma once
// Timezone facade for the `datetime` stdlib module.
//
// The TPy datetime module reaches the OS only through epoch-now (provided
// by tpy/stdlib/time.hpp) and the zone primitives declared here (the
// local-zone lookups plus the named-zone/ZoneInfo set). Everything else --
// calendar math, validation, formatting, parsing, the local-time inverse
// solve -- is pure TPy, so the concrete timezone provider can be swapped
// by swapping the implementing TU without touching TPy stdlib code or
// generated C++.
//
// The current provider is the vendored Howard Hinnant date library
// (runtime/cpp/src/stdlib/date_shim.cpp, built with USE_OS_TZDB so it
// parses the OS zoneinfo database in-process; chosen over localtime_r,
// whose per-call global lock is a measured bottleneck). This header
// deliberately does NOT include the provider's headers -- generated TPy
// TUs see only these declarations.

#include <cstdint>
#include <string>
#include <string_view>

namespace tpy::stdlib::datetime {

// Local UTC offset in seconds east of UTC at the given UTC epoch second.
// The zone is resolved once at first use and pinned for process life:
// the TZ environment variable if set (IANA name via the tz database,
// POSIX rule string via the provider's POSIX reader, empty or
// unparseable -> UTC -- glibc semantics), else the OS default zone
// (/etc/localtime). Unlike libc, a TZ *mutation after first use* is not
// honored (documented divergence).
std::int64_t local_utc_offset_seconds(std::int64_t epoch_seconds);

// The local zone's abbreviation at the given instant ("CET", "EDT",
// "UTC" fallback) -- what libc exposes as tm_zone and CPython surfaces
// via the timezone name of astimezone(None) results.
std::string local_zone_abbrev(std::int64_t epoch_seconds);

// --- Named-zone (ZoneInfo) primitives ---------------------------------
//
// Zones are interned per requested key into a process-global append-only
// table (same model as tz_intern): zone_lookup pins the provider's zone
// handle once and returns a stable positive id; 0 means "no such zone"
// (the exception is raised in TPy -- provider errors never cross the
// facade). Wall-time lookups take the PEP 495 fold argument: at a
// transition, fold=0 selects the pre-transition rule (gap) / the first
// occurrence (fold), fold=1 the post-transition rule / the second
// occurrence -- matching CPython's zoneinfo.

// Intern the zone named `key` (IANA name, exactly as the tz database
// spells it). Idempotent; returns 0 when the database has no such zone.
std::int32_t zone_lookup(std::string_view key);

// The key `zone_id` was interned under ("" for an invalid id).
std::string zone_key(std::int32_t zone_id);

// UTC offset in seconds east of UTC for the zone's wall time
// `wall_seconds` (seconds since the epoch, read as a local clock).
std::int64_t zone_wall_offset_seconds(std::int32_t zone_id,
                                      std::int64_t wall_seconds,
                                      std::int32_t fold);

// DST component of the offset (0 outside DST) at the given wall time.
std::int64_t zone_wall_dst_seconds(std::int32_t zone_id,
                                   std::int64_t wall_seconds,
                                   std::int32_t fold);

// Zone abbreviation ("CET", "CEST", "EDT") at the given wall time.
std::string zone_wall_abbrev(std::int32_t zone_id,
                             std::int64_t wall_seconds,
                             std::int32_t fold);

// UTC offset in seconds at the given UTC instant (fromtimestamp /
// astimezone conversions; the result's fold is derived in TPy).
std::int64_t zone_utc_offset_seconds(std::int32_t zone_id,
                                     std::int64_t epoch_seconds);

// Enumeration of the provider's zone database (available_timezones):
// every enumerated key is constructible via zone_lookup by definition.
// A missing/corrupt database enumerates as empty rather than throwing.
std::int32_t zone_db_count();
std::string zone_db_key_at(std::int32_t index);

} // namespace tpy::stdlib::datetime
