// TPy-owned glue implementing the tpy/stdlib/datetime.hpp timezone facade
// over the vendored Howard Hinnant date library -- the only TU that
// includes its headers. Compiled together with the vendored tz.cpp via
// tpyc/build/date.py, so it is built and linked only when a compiled
// module declares `# tpy: link("date", managed=True)` -- NOT via
// discover_runtime_cpp_sources (always-on), which skips `*_shim.*` files
// for exactly this reason.

#include <tpy/stdlib/datetime.hpp>

#include <date/tz.h>
#include <date/ptz.h>

#include <chrono>
#include <cstdlib>
#include <optional>
#include <string>

namespace tpy::stdlib::datetime {

namespace {

// The zone is resolved once and pinned for process life (USE_OS_TZDB has
// no reload path, and upstream current_zone() re-stats /etc/localtime on
// every call -- only the parsed database is cached). Exceptions must not
// cross the facade: the library throws plain std::runtime_error /
// std::system_error (missing or corrupt tz database, bad TZ value), which
// no TPy except clause can catch -- every failure falls back to fixed UTC
// instead, matching glibc's (and therefore CPython's) silent UTC fallback.
struct TzProvider {
    const date::time_zone* zone = nullptr;
    std::optional<Posix::time_zone> posix;
};

TzProvider resolve_provider() {
    TzProvider p;
    const char* tz = std::getenv("TZ");
    if (tz == nullptr) {
        try {
            p.zone = date::current_zone();
        } catch (...) {
        }
        return p;
    }
    std::string spec(tz);
    if (spec.empty()) {
        return p; // POSIX: empty TZ means UTC
    }
    bool db_only = spec.front() == ':'; // ":Zone" skips the POSIX reading
    if (db_only) {
        spec.erase(0, 1);
    } else {
        // glibc tries the POSIX rule-string reading first ("EST5EDT,...");
        // IANA names ("Europe/Warsaw") fail its grammar and fall through
        // to the database lookup. A bare std/dst pair WITHOUT the rule
        // suffix ("EST5EDT") parses here as a constant permanent-DST
        // offset -- ptz.h has no seasonal transitions for that form,
        // where glibc applies default US DST rules (documented
        // divergence in DATETIME_DESIGN.md).
        try {
            p.posix.emplace(spec);
            return p;
        } catch (...) {
        }
    }
    try {
        p.zone = date::locate_zone(spec);
    } catch (...) {
    }
    return p; // both empty: unparseable TZ degrades to UTC (glibc)
}

const TzProvider& provider() {
    static const TzProvider p = resolve_provider();
    return p;
}

date::sys_info info_at(std::int64_t epoch_seconds) {
    const TzProvider& p = provider();
    auto tp = date::sys_seconds{std::chrono::seconds{epoch_seconds}};
    if (p.posix.has_value()) {
        return p.posix->get_info(tp);
    }
    return p.zone->get_info(tp); // caller guards zone != nullptr
}

} // namespace

std::int64_t local_utc_offset_seconds(std::int64_t epoch_seconds) {
    const TzProvider& p = provider();
    if (p.zone == nullptr && !p.posix.has_value()) {
        return 0;
    }
    try {
        return info_at(epoch_seconds).offset.count();
    } catch (...) {
        return 0;
    }
}

std::string local_zone_abbrev(std::int64_t epoch_seconds) {
    const TzProvider& p = provider();
    if (p.zone == nullptr && !p.posix.has_value()) {
        return "UTC";
    }
    try {
        return info_at(epoch_seconds).abbrev;
    } catch (...) {
        return "UTC";
    }
}

} // namespace tpy::stdlib::datetime
