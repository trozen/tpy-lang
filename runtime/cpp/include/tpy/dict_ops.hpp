/**
 * TurboPython Runtime - Dict Operations
 *
 * Dict-specific helpers not covered by the generic dunder protocol:
 * get (returns optional), pop (with and without default), and DictPrinter.
 *
 * Key arguments use a separate KeyArg template parameter (deduced from the
 * argument) to accept e.g. string_view or const char* where K=std::string.
 */

#pragma once

#include <cstdint>
#include <iostream>
#include <optional>

#include "core.hpp"
#include "ordered_map.hpp"

namespace tpy {

// -- Methods ----------------------------------------------------------------

// d.get(key) -> Optional[V]
template<typename K, typename V, typename KeyArg>
std::optional<V> dict_get(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) return std::nullopt;
    return (*it).second;
}

// d.pop(key) -> V (panics on missing)
template<typename K, typename V, typename KeyArg>
V dict_pop(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) {
        tpy_panic("KeyError");
    }
    V result = std::move((*it).second);
    m.erase(it);
    return result;
}

// d.pop(key, default) -> V
template<typename K, typename V, typename KeyArg>
V dict_pop_default(ordered_map<K, V>& m, const KeyArg& key, V def) {
    auto it = m.find(K(key));
    if (it == m.items_end()) return def;
    V result = std::move((*it).second);
    m.erase(it);
    return result;
}

// -- Printing ---------------------------------------------------------------

namespace detail {
// Forward declaration -- defined in printing.hpp
template <typename T> void print_element(std::ostream& os, const T& elem);
}  // namespace detail

template<typename K, typename V>
struct DictPrinter {
    const ordered_map<K, V>& value;
    explicit DictPrinter(const ordered_map<K, V>& v) : value(v) {}
};

template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const DictPrinter<K, V>& p) {
    os << '{';
    bool first = true;
    for (auto it = p.value.items_begin(); it != p.value.items_end(); ++it) {
        auto&& [k, v] = *it;
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, k);
        os << ": ";
        detail::print_element(os, v);
    }
    os << '}';
    return os;
}

}  // namespace tpy
