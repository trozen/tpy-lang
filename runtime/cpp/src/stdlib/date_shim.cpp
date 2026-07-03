// TPy-owned glue implementing the tpy/stdlib/datetime.hpp timezone facade
// over the vendored Howard Hinnant date library -- the only TU that
// includes its headers. Compiled together with the vendored tz.cpp via
// tpyc/build/date.py, so it is built and linked only when a compiled
// module declares `# tpy: link("date", managed=True)` -- NOT via
// discover_runtime_cpp_sources (always-on), which skips `*_shim.*` files
// for exactly this reason.

#include <tpy/stdlib/datetime.hpp>

#include <date/tz.h>

#include <chrono>

namespace tpy::stdlib::datetime {

std::int64_t local_utc_offset_seconds(std::int64_t epoch_seconds) {
    // Upstream current_zone() re-stats /etc/localtime on every call (only
    // the parsed database is cached), so resolve the zone once and keep the
    // pointer -- it is stable for process lifetime (USE_OS_TZDB has no
    // reload path). Exceptions must not cross this boundary: the library
    // throws plain std::runtime_error/std::system_error (missing or corrupt
    // tz database), which no TPy except clause can catch -- fall back to
    // UTC (offset 0) instead, matching glibc's (and therefore CPython's)
    // silent UTC fallback on tz-db-less hosts. A transient failure pins the
    // fallback for process life.
    static const date::time_zone* zone = []() -> const date::time_zone* {
        try {
            return date::current_zone();
        } catch (...) {
            return nullptr;
        }
    }();
    if (zone == nullptr) {
        return 0;
    }
    try {
        auto tp = date::sys_seconds{std::chrono::seconds{epoch_seconds}};
        return zone->get_info(tp).offset.count();
    } catch (...) {
        return 0;
    }
}

} // namespace tpy::stdlib::datetime
