/**
 * TurboPython Runtime - Set Operations
 *
 * Set-specific helpers: remove (panicking), pop, set algebra operations,
 * constructors from iterables, and SetPrinter.
 */

#pragma once

#include <cstdint>
#include <iostream>
#include <ranges>
#include <sstream>

#include "core.hpp"
#include "ordered_set.hpp"
#include "printing.hpp"

namespace tpy {

// -- Constructors -----------------------------------------------------------

// set(native_iterable) -- construct from range
template<typename T, std::ranges::input_range R>
ordered_set<T> set_from_range(R&& range) {
    ordered_set<T> result;
    for (auto&& elem : range) {
        result.insert(elem);
    }
    return result;
}

// set(iterator) -- construct from user-defined iterator
template<typename T, typename Iter>
ordered_set<T> set_collect(Iter&& iter) {
    ordered_set<T> result;
    for (;;) {
        auto __r = iter.__next__();
        if (!__r.has_value()) break;
        result.insert(std::move(*__r));
    }
    return result;
}

// -- Copy -------------------------------------------------------------------

template<typename T>
ordered_set<T> set_copy(const ordered_set<T>& s) {
    return ordered_set<T>(s);
}

// -- Methods ----------------------------------------------------------------

// s.remove(value) -- panics if not present
template<typename T>
void set_remove(ordered_set<T>& s, const T& value) {
    if (!s.erase(value)) {
        tpy_panic("KeyError");
    }
}

// s.pop() -- remove and return first element (insertion order), panic if empty
template<typename T>
T set_pop(ordered_set<T>& s) {
    if (s.empty()) {
        tpy_panic("KeyError: pop from an empty set");
    }
    T result = s.front();
    s.pop_front();
    return result;
}

// -- Set algebra ------------------------------------------------------------

template<typename T>
ordered_set<T> set_union(const ordered_set<T>& a, const ordered_set<T>& b) {
    ordered_set<T> result(a);
    for (auto& v : b) {
        result.insert(v);
    }
    return result;
}

template<typename T>
ordered_set<T> set_intersection(const ordered_set<T>& a, const ordered_set<T>& b) {
    ordered_set<T> result;
    for (auto& v : a) {
        if (b.contains(v)) result.insert(v);
    }
    return result;
}

template<typename T>
ordered_set<T> set_difference(const ordered_set<T>& a, const ordered_set<T>& b) {
    ordered_set<T> result;
    for (auto& v : a) {
        if (!b.contains(v)) result.insert(v);
    }
    return result;
}

template<typename T>
ordered_set<T> set_symmetric_difference(const ordered_set<T>& a, const ordered_set<T>& b) {
    ordered_set<T> result;
    for (auto& v : a) {
        if (!b.contains(v)) result.insert(v);
    }
    for (auto& v : b) {
        if (!a.contains(v)) result.insert(v);
    }
    return result;
}

template<typename T>
bool set_issubset(const ordered_set<T>& a, const ordered_set<T>& b) {
    if (a.size() > b.size()) return false;
    for (auto& v : a) {
        if (!b.contains(v)) return false;
    }
    return true;
}

template<typename T>
bool set_issuperset(const ordered_set<T>& a, const ordered_set<T>& b) {
    return set_issubset(b, a);
}

template<typename T>
bool set_strict_subset(const ordered_set<T>& a, const ordered_set<T>& b) {
    return a.size() < b.size() && set_issubset(a, b);
}

template<typename T>
bool set_strict_superset(const ordered_set<T>& a, const ordered_set<T>& b) {
    return a.size() > b.size() && set_issubset(b, a);
}

template<typename T>
bool set_isdisjoint(const ordered_set<T>& a, const ordered_set<T>& b) {
    const auto& smaller = (a.size() <= b.size()) ? a : b;
    const auto& larger = (a.size() <= b.size()) ? b : a;
    for (auto& v : smaller) {
        if (larger.contains(v)) return false;
    }
    return true;
}

// -- In-place updates -------------------------------------------------------

template<typename T>
void set_update(ordered_set<T>& s, const ordered_set<T>& other) {
    for (auto& v : other) {
        s.insert(v);
    }
}

template<typename T>
void set_intersection_update(ordered_set<T>& s, const ordered_set<T>& other) {
    ordered_set<T> result;
    for (auto& v : s) {
        if (other.contains(v)) result.insert(v);
    }
    s = std::move(result);
}

template<typename T>
void set_difference_update(ordered_set<T>& s, const ordered_set<T>& other) {
    if (&s == &other) { s.clear(); return; }
    for (auto& v : other) {
        s.erase(v);
    }
}

template<typename T>
void set_symmetric_difference_update(ordered_set<T>& s, const ordered_set<T>& other) {
    if (&s == &other) { s.clear(); return; }
    for (auto& v : other) {
        if (!s.erase(v)) {
            s.insert(v);
        }
    }
}

// -- ordered_set::__iter__() definition (deferred -- needs native_iterator) -

template<typename T>
auto ordered_set<T>::__iter__() const {
    return native_iterator<const_iterator, T>{begin(), end()};
}

// -- Dunder overloads -------------------------------------------------------

template<typename T>
int32_t __len__(const ordered_set<T>& s) {
    return s.size();
}

template<typename T>
bool __bool__(const ordered_set<T>& s) {
    return !s.empty();
}

// -- Printing ---------------------------------------------------------------

namespace detail {
// Forward declaration -- defined in printing.hpp
template <typename T> void print_element(std::ostream& os, const T& elem);
}  // namespace detail

template<typename T>
struct SetPrinter {
    const ordered_set<T>& value;
    explicit SetPrinter(const ordered_set<T>& v) : value(v) {}
};

template<typename T>
std::ostream& operator<<(std::ostream& os, const SetPrinter<T>& p) {
    if (p.value.empty()) {
        return os << "set()";
    }
    os << '{';
    bool first = true;
    for (auto& v : p.value) {
        if (!first) os << ", ";
        first = false;
        detail::print_element(os, v);
    }
    os << '}';
    return os;
}

template<typename T>
std::string set_to_str(const ordered_set<T>& s) {
    std::ostringstream oss;
    oss << SetPrinter<T>(s);
    return oss.str();
}

// Nested container support
namespace detail {
template<typename T>
void print_element(std::ostream& os, const ordered_set<T>& elem) {
    os << SetPrinter<T>(elem);
}
}  // namespace detail

// ValuePrinter specialization so sets inside generic containers print correctly
template<typename T>
std::ostream& operator<<(std::ostream& os, const ValuePrinter<ordered_set<T>>& p) {
    return os << SetPrinter<T>(p.value);
}

}  // namespace tpy
