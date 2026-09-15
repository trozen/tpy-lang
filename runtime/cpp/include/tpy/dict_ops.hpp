/**
 * TurboPython Runtime - Dict Operations
 *
 * Dict-specific helpers not covered by the generic dunder protocol:
 * get (returns optional), pop (with and without default), and DictPrinter.
 *
 * Key arguments use a separate KeyArg template parameter (deduced from the
 * argument) and are forwarded to `ordered_map::find` in the form they arrive
 * in: a read-form key probes the table directly, so a dict lookup does not
 * build a key (see lookup_key.hpp). Only `setdefault`'s miss path stores.
 */

#pragma once

#include <algorithm>
#include <concepts>
#include <cstdint>
#include <iostream>
#include <optional>
#include <ranges>
#include <sstream>
#include <tuple>
#include <type_traits>
#include <utility>

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
        auto&& __item = unwrap_ref_move(*__r);
        result.insert_or_assign(std::get<0>(std::move(__item)), std::get<1>(std::move(__item)));
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
    auto it = m.find(key);
    if (it == m.items_end()) return nullptr;
    return &((*it).second);
}

// const overload for readonly dict access
template<typename K, typename V, typename KeyArg>
const V* dict_get(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(key);
    if (it == m.items_end()) return nullptr;
    return &((*it).second);
}

// d.pop(key) -> V (throws KeyError on missing)
template<typename K, typename V, typename KeyArg>
V dict_pop(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(key);
    if (it == m.items_end()) {
        raise_key_error("KeyError");
    }
    V result = std::move((*it).second);
    m.erase(it);
    return result;
}

// The message the three default-taking natives share when the default cannot
// become the dict's value type. Each spells it as a static_assert in the body
// rather than as a `requires` clause: a failed constraint would REMOVE the only
// candidate, and the toolchain would report "no matching function" instead.
#define TPY_DICT_DEFAULT_ASSERT(V, DefArg)                                   \
    static_assert(std::constructible_from<V, DefArg&&>,                      \
                  "the default passed to dict get/pop/setdefault must be "   \
                  "constructible into the dict's value type")

// d.pop(key, default) -> V
// The default arrives in the BORROW form the stub declares (`default: V`):
// a `str` one is a view, a `bytes` one a span, a BigInt one still an `int`
// literal -- so it gets its own parameter, like the heterogeneous key, and
// the owned V this returns is built here rather than deduced from it.
template<typename K, typename V, typename KeyArg, typename DefArg>
V dict_pop_default(ordered_map<K, V>& m, const KeyArg& key, DefArg&& def) {
    TPY_DICT_DEFAULT_ASSERT(V, DefArg);
    auto it = m.find(key);
    if (it == m.items_end()) return V(std::forward<DefArg>(def));
    V result = std::move((*it).second);
    m.erase(it);
    return result;
}

// d.get(key, default) -> V
template<typename K, typename V, typename KeyArg, typename DefArg>
V dict_get_default(const ordered_map<K, V>& m, const KeyArg& key, DefArg&& def) {
    TPY_DICT_DEFAULT_ASSERT(V, DefArg);
    auto it = m.find(key);
    if (it == m.items_end()) return V(std::forward<DefArg>(def));
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

// d.setdefault(key, default) -> V& into the map (CPython returns the stored
// object; the borrow makes `d.setdefault(k, []).append(x)` reach the dict).
// Unlike its siblings this one STORES the default, so it builds the owned V
// once, on the miss path, and moves it into the node -- but V is deduced from
// the map either way, so a default in borrow form (a `str` view, a `bytes`
// span) reaches it like any other.
template<typename K, typename V, typename KeyArg, typename DefArg>
V& dict_setdefault(ordered_map<K, V>& m, const KeyArg& key, DefArg&& def) {
    TPY_DICT_DEFAULT_ASSERT(V, DefArg);
    auto it = m.find(key);
    if (it != m.items_end()) return (*it).second;
    // Only the MISS path stores, so only it builds the key and the value.
    K k(key);
    m.insert_or_assign(k, V(std::forward<DefArg>(def)));
    return (*m.find(k)).second;
}

// A header-only runtime leaks no macro into the generated TU: a TPy
// function of this name called with two arguments would otherwise expand it.
#undef TPY_DICT_DEFAULT_ASSERT

// -- Views (zero-allocation wrappers for keys/values/items iteration) -------

template<typename K, typename V>
struct dict_keys_view {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->begin(); }
    auto end() const { return map_->end(); }
    int32_t size() const { return map_->size(); }
    // Forward the key generically so the map's contains overload set
    // applies (incl. the wider-integer key arm: out-of-range -> False).
    template<typename KeyArg>
    bool contains(const KeyArg& key) const { return map_->contains(key); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), K>{begin(), end()};
    }
};

// Mutable views alias the map so element mutation reaches the dict
// (CPython `for v in d.values(): v.append(...)` semantics). The `const V`
// partial specializations are the readonly-receiver form (TPy renders
// `dict_values[K, readonly[V]]` as `dict_values_view<K, const V>`).

template<typename K, typename V>
struct dict_values_view {
    ordered_map<K, V>* map_;
    auto begin() const { return map_->values_begin(); }
    auto end() const { return map_->values_end(); }
    int32_t size() const { return map_->size(); }
    // Generic key: heterogeneous == (a BigInt key against fixed-int
    // values compares by value; out-of-range is simply absent).
    template<typename ValArg>
        requires requires(const V& v, const ValArg& a) {
            { v == a } -> std::convertible_to<bool>;
        }
    bool contains(const ValArg& value) const { return std::find(begin(), end(), value) != end(); }

    // A wider-integer key whose == with V is not usable (unsigned V:
    // `uint32_t == BigInt` is ambiguous between the int64/uint64 ctors):
    // narrow-then-find, out-of-range -> absent (False), like the map's key
    // overload.
    template<typename ValArg>
        requires (std::is_integral_v<V>
                  && !requires(const V& v, const ValArg& a) {
                         { v == a } -> std::convertible_to<bool>;
                     }
                  && requires(const ValArg& a, V& out) {
                         { a.template to_fixed_try<V>(out) } -> std::convertible_to<bool>;
                     })
    bool contains(const ValArg& value) const {
        V narrowed;
        return value.template to_fixed_try<V>(narrowed)
               && std::find(begin(), end(), narrowed) != end();
    }

    auto __iter__() const {
        return native_iterator<decltype(begin()), V>{begin(), end()};
    }
};

template<typename K, typename V>
struct dict_values_view<K, const V> {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->values_begin(); }
    auto end() const { return map_->values_end(); }
    int32_t size() const { return map_->size(); }
    // Generic key: heterogeneous == (a BigInt key against fixed-int
    // values compares by value; out-of-range is simply absent).
    template<typename ValArg>
        requires requires(const V& v, const ValArg& a) {
            { v == a } -> std::convertible_to<bool>;
        }
    bool contains(const ValArg& value) const { return std::find(begin(), end(), value) != end(); }

    // A wider-integer key whose == with V is not usable (unsigned V:
    // `uint32_t == BigInt` is ambiguous between the int64/uint64 ctors):
    // narrow-then-find, out-of-range -> absent (False), like the map's key
    // overload.
    template<typename ValArg>
        requires (std::is_integral_v<V>
                  && !requires(const V& v, const ValArg& a) {
                         { v == a } -> std::convertible_to<bool>;
                     }
                  && requires(const ValArg& a, V& out) {
                         { a.template to_fixed_try<V>(out) } -> std::convertible_to<bool>;
                     })
    bool contains(const ValArg& value) const {
        V narrowed;
        return value.template to_fixed_try<V>(narrowed)
               && std::find(begin(), end(), narrowed) != end();
    }

    auto __iter__() const {
        return native_iterator<decltype(begin()), V>{begin(), end()};
    }
};

template<typename K, typename V>
struct dict_items_view {
    ordered_map<K, V>* map_;
    auto begin() const { return map_->tuple_items_begin(); }
    auto end() const { return map_->tuple_items_end(); }
    int32_t size() const { return map_->size(); }
    bool contains(const std::tuple<K, V>& item) const { return std::find(begin(), end(), item) != end(); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), std::tuple<K, V>>{begin(), end()};
    }
};

template<typename K, typename V>
struct dict_items_view<K, const V> {
    const ordered_map<K, V>* map_;
    auto begin() const { return map_->tuple_items_begin(); }
    auto end() const { return map_->tuple_items_end(); }
    int32_t size() const { return map_->size(); }
    bool contains(const std::tuple<K, V>& item) const { return std::find(begin(), end(), item) != end(); }

    auto __iter__() const {
        return native_iterator<decltype(begin()), std::tuple<K, V>>{begin(), end()};
    }
};

// All three views are value types (a view is copied, not aliased, at a generic
// slot) but each holds a pointer into a dict it does not own and may not have
// exclusive access to -- so neither Send nor Sync, whatever the element is.
// One row per family covers its `const V` readonly specialization too.
template<typename K, typename V> struct is_value_type<dict_keys_view<K, V>> : std::true_type {};
template<typename K, typename V> struct is_value_type<dict_values_view<K, V>> : std::true_type {};
template<typename K, typename V> struct is_value_type<dict_items_view<K, V>> : std::true_type {};
template<typename K, typename V> struct is_send<dict_keys_view<K, V>> : std::false_type {};
template<typename K, typename V> struct is_send<dict_values_view<K, V>> : std::false_type {};
template<typename K, typename V> struct is_send<dict_items_view<K, V>> : std::false_type {};
template<typename K, typename V> struct is_sync<dict_keys_view<K, V>> : std::false_type {};
template<typename K, typename V> struct is_sync<dict_values_view<K, V>> : std::false_type {};
template<typename K, typename V> struct is_sync<dict_items_view<K, V>> : std::false_type {};

template<typename K, typename V>
dict_keys_view<K, V> dict_keys(const ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_values_view<K, V> dict_values(ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_values_view<K, const V> dict_values(const ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_items_view<K, V> dict_items(ordered_map<K, V>& m) { return {&m}; }
template<typename K, typename V>
dict_items_view<K, const V> dict_items(const ordered_map<K, V>& m) { return {&m}; }

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
