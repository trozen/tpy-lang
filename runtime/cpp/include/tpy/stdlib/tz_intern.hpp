#pragma once
// Timezone-name intern table for the `datetime` stdlib module.
//
// datetime/timezone store names as int32 ids so both stay small,
// trivially-copyable value types; the strings live here, process-global
// and append-only (entries are never freed -- ids stay valid for process
// life). Id 0 is reserved for "no name" (an unnamed fixed-offset
// timezone); the empty string is a legal, distinct name and interns like
// any other. Header-only and independent of the tz-backend facade: pure
// calendar code (`from datetime import timedelta`) must not drag the
// managed `date` lib in, so this must NOT live in datetime.hpp.
//
// Interning happens on timezone construction and name lookups on
// tzname()/repr()/%Z -- both cold paths; offset math never touches this.

#include <cstdint>
#include <deque>
#include <mutex>
#include <string>
#include <unordered_map>

namespace tpy::stdlib::tz_intern {

struct Table {
    std::mutex mu;
    std::deque<std::string> names;               // index i <-> id i + 1
    std::unordered_map<std::string, std::int32_t> ids;
};

inline Table& table() {
    static Table t;
    return t;
}

// Intern a name, returning its stable positive id (idempotent).
inline std::int32_t intern_name(std::string_view name) {
    Table& t = table();
    std::lock_guard<std::mutex> lock(t.mu);
    std::string key(name);
    auto it = t.ids.find(key);
    if (it != t.ids.end()) {
        return it->second;
    }
    t.names.push_back(key);
    std::int32_t id = static_cast<std::int32_t>(t.names.size());
    t.ids.emplace(std::move(key), id);
    return id;
}

// Name for a previously interned id. Callers only hold ids minted by
// intern_name, so an unknown id is a programming error; return "" rather
// than throwing across the native boundary.
inline std::string name_at(std::int32_t id) {
    Table& t = table();
    std::lock_guard<std::mutex> lock(t.mu);
    if (id < 1 || static_cast<std::size_t>(id) > t.names.size()) {
        return std::string();
    }
    return t.names[static_cast<std::size_t>(id) - 1];
}

} // namespace tpy::stdlib::tz_intern
