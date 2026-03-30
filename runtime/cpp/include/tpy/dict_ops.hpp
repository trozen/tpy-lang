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

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <optional>
#include <ranges>
#include <sstream>
#include <tuple>

#include "core.hpp"
#include "next_iter.hpp"
#include "ordered_map.hpp"

namespace tpy {

// native_iterator is defined in dunder.hpp

// -- Constructors -----------------------------------------------------------

// dict(native_iterable) -- construct from range of tuples
template<typename K, typename V, std::ranges::input_range R>
ordered_map<K, V> dict_from_pairs(R&& range) {
    ordered_map<K, V> result;
    for (auto&& elem : range) {
        result.insert_or_assign(std::get<0>(elem), std::get<1>(elem));
    }
    return result;
}

// dict(iterator) -- construct from user-defined iterator of tuples
template<typename K, typename V, typename Iter>
ordered_map<K, V> dict_collect_pairs(Iter&& iter) {
    ordered_map<K, V> result;
    for (;;) {
        auto __r = iter.__next__();
        if (!__r.has_value()) break;
        auto&& __item = unwrap_ref(*__r);
        result.insert_or_assign(std::get<0>(__item), std::get<1>(__item));
    }
    return result;
}

// dict_construct -- unified dict construction from any iterable of pairs.
// Uses begin/end for std::ranges::input_range, __next__() otherwise.
template<typename K, typename V, typename Arg>
ordered_map<K, V> dict_construct(Arg&& arg) {
    if constexpr (std::ranges::input_range<std::remove_cvref_t<Arg>>) {
        return dict_from_pairs<K, V>(std::forward<Arg>(arg));
    } else {
        return dict_collect_pairs<K, V>(std::forward<Arg>(arg));
    }
}

// -- Methods ----------------------------------------------------------------

// d.get(key) -> V* (nullptr if missing, pointer into the map)
template<typename K, typename V, typename KeyArg>
V* dict_get(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) return nullptr;
    return &((*it).second);
}

// const overload for readonly dict access
template<typename K, typename V, typename KeyArg>
const V* dict_get(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) return nullptr;
    return &((*it).second);
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

// d.get(key, default) -> V
template<typename K, typename V, typename KeyArg>
V dict_get_default(const ordered_map<K, V>& m, const KeyArg& key, V def) {
    auto it = m.find(K(key));
    if (it == m.items_end()) return def;
    return (*it).second;
}

// d.update(other)
template<typename K, typename V>
void dict_update(ordered_map<K, V>& m, const ordered_map<K, V>& other) {
    for (auto it = other.items_begin(); it != other.items_end(); ++it) {
        auto&& [k, v] = *it;
        m.insert_or_assign(k, v);
    }
}

// d.setdefault(key, default) -> V
template<typename K, typename V, typename KeyArg>
V dict_setdefault(ordered_map<K, V>& m, const KeyArg& key, V def) {
    K k(key);
    auto it = m.find(k);
    if (it != m.items_end()) return (*it).second;
    m.insert_or_assign(k, std::move(def));
    return (*m.find(k)).second;
}

// -- Views (zero-allocation wrappers for keys/values/items iteration) -------

template<typename K, typename V>
struct dict_keys_view {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->begin(); }
    auto end() const { return map_->end(); }
    int32_t size() const { return map_->size(); }
    bool contains(const K& key) const { return map_->contains(key); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), K>{begin(), end()};
    }
};

template<typename K, typename V>
struct dict_values_view {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->values_begin(); }
    auto end() const { return map_->values_end(); }
    int32_t size() const { return map_->size(); }
    bool contains(const V& value) const { return std::find(begin(), end(), value) != end(); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), V>{begin(), end()};
    }
};

template<typename K, typename V>
struct dict_items_view {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->tuple_items_begin(); }
    auto end() const { return map_->tuple_items_end(); }
    int32_t size() const { return map_->size(); }
    bool contains(const std::tuple<K, V>& item) const { return std::find(begin(), end(), item) != end(); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), std::tuple<K, V>>{begin(), end()};
    }
};

template<typename K, typename V>
dict_keys_view<K, V> dict_keys(const ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_values_view<K, V> dict_values(const ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_items_view<K, V> dict_items(const ordered_map<K, V>& m) { return {&m}; }

// -- ordered_map::__iter__() definition (deferred -- needs native_iterator) -

template<typename K, typename V>
auto ordered_map<K, V>::__iter__() const {
    return native_iterator<const_iterator, K>{begin(), end()};
}

// -- View __len__ -----------------------------------------------------------

template<typename K, typename V>
int32_t __len__(const dict_keys_view<K, V>& v) { return v.size(); }
template<typename K, typename V>
int32_t __len__(const dict_values_view<K, V>& v) { return v.size(); }
template<typename K, typename V>
int32_t __len__(const dict_items_view<K, V>& v) { return v.size(); }

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

// Nested container support
namespace detail {
template<typename K, typename V>
void print_element(std::ostream& os, const ordered_map<K, V>& elem) {
    os << DictPrinter<K, V>(elem);
}
}  // namespace detail

// ValuePrinter specialization so dicts inside generic containers print correctly
template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const ValuePrinter<ordered_map<K, V>>& p) {
    return os << DictPrinter<K, V>(p.value);
}

template<typename K, typename V>
std::string dict_to_str(const ordered_map<K, V>& m) {
    std::ostringstream oss;
    oss << DictPrinter<K, V>(m);
    return oss.str();
}

// -- View printing ----------------------------------------------------------

template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const dict_keys_view<K, V>& v) {
    os << "dict_keys([";
    bool first = true;
    for (auto it = v.begin(); it != v.end(); ++it) {
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, *it);
    }
    return os << "])";
}

template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const dict_values_view<K, V>& v) {
    os << "dict_values([";
    bool first = true;
    for (auto it = v.begin(); it != v.end(); ++it) {
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, *it);
    }
    return os << "])";
}

template<typename K, typename V>
std::ostream& operator<<(std::ostream& os, const dict_items_view<K, V>& v) {
    os << "dict_items([";
    bool first = true;
    for (auto it = v.begin(); it != v.end(); ++it) {
        if (!first) os << ", ";
        first = false;
        auto&& [k, val] = *it;
        os << '(';
        detail::print_element(os, k);
        os << ", ";
        detail::print_element(os, val);
        os << ')';
    }
    return os << "])";
}

}  // namespace tpy
