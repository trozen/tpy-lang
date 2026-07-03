#pragma once
// Timezone facade for the `datetime` stdlib module.
//
// The TPy datetime module reaches the OS through exactly two primitives:
// epoch-now (provided by tpy/stdlib/time.hpp) and the local-UTC-offset
// lookup declared here. Everything else -- calendar math, validation,
// formatting -- is pure TPy, so the concrete timezone provider can be
// swapped by swapping the implementing TU without touching TPy stdlib
// code or generated C++.
//
// The current provider is the vendored Howard Hinnant date library
// (runtime/cpp/src/stdlib/date_shim.cpp, built with USE_OS_TZDB so it
// parses the OS zoneinfo database in-process; chosen over localtime_r,
// whose per-call global lock is a measured bottleneck). This header
// deliberately does NOT include the provider's headers -- generated TPy
// TUs see only this declaration.

#include <cstdint>

namespace tpy::stdlib::datetime {

// Local UTC offset in seconds east of UTC at the given UTC epoch second,
// per the OS timezone database's current zone (/etc/localtime). Note:
// unlike libc (and therefore CPython), the provider does not consult the
// TZ environment variable.
std::int64_t local_utc_offset_seconds(std::int64_t epoch_seconds);

} // namespace tpy::stdlib::datetime
