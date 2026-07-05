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
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

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

// Named-zone intern table (mirrors tz_intern: mutex-guarded, append-only,
// stable positive ids, id 0 = not found). Keyed by the REQUESTED key so
// zone_key round-trips what the user wrote even for tz-db links.
struct ZoneTable {
    std::mutex mu;
    std::vector<const date::time_zone*> zones;
    std::vector<std::string> keys;
    std::unordered_map<std::string, std::int32_t> ids;
};

ZoneTable& zone_table() {
    static ZoneTable t;
    return t;
}

const date::time_zone* zone_at(std::int32_t zone_id) {
    ZoneTable& t = zone_table();
    std::lock_guard<std::mutex> lock(t.mu);
    if (zone_id < 1 || static_cast<std::size_t>(zone_id) > t.zones.size()) {
        return nullptr;
    }
    return t.zones[static_cast<std::size_t>(zone_id) - 1];
}

// PEP 495 selection over the provider's local_info: fold=0 takes the
// pre-transition rule (gap) / first occurrence (fold), fold=1 the
// post-transition rule / second occurrence.
date::sys_info wall_info_at(const date::time_zone* zone,
                            std::int64_t wall_seconds, std::int32_t fold) {
    auto lp = date::local_seconds{std::chrono::seconds{wall_seconds}};
    date::local_info li = zone->get_info(lp);
    if (li.result == date::local_info::unique || fold == 0) {
        return li.first;
    }
    return li.second;
}

// Under USE_OS_TZDB the tzfile carries only an is_dst FLAG, and the
// library stores the sentinel save=1min for DST intervals -- the real
// saving must be derived as this interval's offset minus a neighboring
// standard interval's offset (CPython's zoneinfo uses the same
// neighbor heuristic on the raw transitions).
std::int64_t dst_seconds_for(const date::time_zone* zone,
                             const date::sys_info& info) {
    using std::chrono::minutes;
    using std::chrono::seconds;
    if (info.save == minutes{0}) {
        return 0;
    }
    date::sys_info probe = info;
    for (int i = 0; i < 8; ++i) {
        if (probe.begin == date::sys_seconds::min()) {
            break;
        }
        probe = zone->get_info(probe.begin - seconds{1});
        if (probe.save == minutes{0}) {
            return (info.offset - probe.offset).count();
        }
    }
    probe = info;
    for (int i = 0; i < 8; ++i) {
        if (probe.end == date::sys_seconds::max()) {
            break;
        }
        probe = zone->get_info(probe.end);
        if (probe.save == minutes{0}) {
            return (info.offset - probe.offset).count();
        }
    }
    // No standard neighbor found (permanent-DST zone): CPython's
    // heuristic falls back to a 1h saving.
    return 3600;
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

std::int32_t zone_lookup(std::string_view key_view) {
    std::string key(key_view);
    ZoneTable& t = zone_table();
    std::lock_guard<std::mutex> lock(t.mu);
    auto it = t.ids.find(key);
    if (it != t.ids.end()) {
        return it->second;
    }
    const date::time_zone* zone = nullptr;
    try {
        zone = date::locate_zone(key);
    } catch (...) {
        // locate_zone throws for unknown keys AND for a missing/corrupt
        // tz database; both surface as "not found" and TPy raises.
    }
    if (zone == nullptr) {
        return 0;
    }
    t.zones.push_back(zone);
    t.keys.push_back(key);
    auto id = static_cast<std::int32_t>(t.zones.size());
    t.ids.emplace(key, id);
    return id;
}

std::string zone_key(std::int32_t zone_id) {
    ZoneTable& t = zone_table();
    std::lock_guard<std::mutex> lock(t.mu);
    if (zone_id < 1 || static_cast<std::size_t>(zone_id) > t.keys.size()) {
        return "";
    }
    return t.keys[static_cast<std::size_t>(zone_id) - 1];
}

std::int64_t zone_wall_offset_seconds(std::int32_t zone_id,
                                      std::int64_t wall_seconds,
                                      std::int32_t fold) {
    const date::time_zone* zone = zone_at(zone_id);
    if (zone == nullptr) {
        return 0;
    }
    try {
        return wall_info_at(zone, wall_seconds, fold).offset.count();
    } catch (...) {
        return 0;
    }
}

std::int64_t zone_wall_dst_seconds(std::int32_t zone_id,
                                   std::int64_t wall_seconds,
                                   std::int32_t fold) {
    const date::time_zone* zone = zone_at(zone_id);
    if (zone == nullptr) {
        return 0;
    }
    try {
        return dst_seconds_for(zone, wall_info_at(zone, wall_seconds, fold));
    } catch (...) {
        return 0;
    }
}

std::string zone_wall_abbrev(std::int32_t zone_id,
                             std::int64_t wall_seconds,
                             std::int32_t fold) {
    const date::time_zone* zone = zone_at(zone_id);
    if (zone == nullptr) {
        return "UTC";
    }
    try {
        return wall_info_at(zone, wall_seconds, fold).abbrev;
    } catch (...) {
        return "UTC";
    }
}

std::int64_t zone_utc_offset_seconds(std::int32_t zone_id,
                                     std::int64_t epoch_seconds) {
    const date::time_zone* zone = zone_at(zone_id);
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

std::int32_t zone_db_count() {
    try {
        return static_cast<std::int32_t>(date::get_tzdb().zones.size());
    } catch (...) {
        return 0;
    }
}

std::string zone_db_key_at(std::int32_t index) {
    try {
        const auto& zones = date::get_tzdb().zones;
        if (index < 0 || static_cast<std::size_t>(index) >= zones.size()) {
            return "";
        }
        return zones[static_cast<std::size_t>(index)].name();
    } catch (...) {
        return "";
    }
}

} // namespace tpy::stdlib::datetime
