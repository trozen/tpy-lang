#pragma once
// Timezone facade for the `datetime` stdlib module.
//
// The TPy datetime module reaches the OS through exactly three primitives:
// epoch-now (provided by tpy/stdlib/time.hpp) and the local-zone lookups
// declared here. Everything else -- calendar math, validation, formatting,
// parsing, the local-time inverse solve -- is pure TPy, so the concrete
// timezone provider can be swapped by swapping the implementing TU without
// touching TPy stdlib code or generated C++.
//
// The current provider is the vendored Howard Hinnant date library
// (runtime/cpp/src/stdlib/date_shim.cpp, built with USE_OS_TZDB so it
// parses the OS zoneinfo database in-process; chosen over localtime_r,
// whose per-call global lock is a measured bottleneck). This header
// deliberately does NOT include the provider's headers -- generated TPy
// TUs see only these declarations.

#include <cstdint>
#include <string>

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

} // namespace tpy::stdlib::datetime
