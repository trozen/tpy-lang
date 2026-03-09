/**
 * TurboPython Runtime - Dunder Free Functions
 *
 * Protocol free functions (__len__, __getitem__, __setitem__) that bridge
 * Python dunder methods to C++ types. Overloads handle index normalization
 * and bounds checking (Python semantics). Default templates forward to the
 * user type's own dunder method.
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <format>
#include <functional>
#include <optional>
#include <ranges>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "bigint.hpp"
#include "format.hpp"
#include "container_ops.hpp"
#include "ordered_map.hpp"
#include "span_iter.hpp"

namespace tpy {

// =============================================
// tpy::__len__
// =============================================

// Overload: std::vector (most specific, checked first)
template<typename T>
int32_t __len__(const std::vector<T>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::array
template<typename T, std::size_t N>
int32_t __len__(const std::array<T, N>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::span (const and non-const)
template<typename T>
int32_t __len__(std::span<const T> x) {
    return static_cast<int32_t>(x.size());
}

template<typename T>
int32_t __len__(std::span<T> x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::string
inline int32_t __len__(const std::string& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::string_view
inline int32_t __len__(std::string_view x) {
    return static_cast<int32_t>(x.size());
}

// Overload: const char* (string literals)
inline int32_t __len__(const char* x) {
    return static_cast<int32_t>(std::string_view(x).size());
}

// Overload: ordered_map (dict)
template<typename K, typename V>
int32_t __len__(const ordered_map<K, V>& x) {
    return x.size();
}

// Default template: user types that define __len__() method
// This is checked last due to the requires clause
template<typename T>
    requires requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; }
int32_t __len__(const T& x) {
    return x.__len__();
}

// User types returning BigInt from __len__()
template<typename T>
    requires (requires(const T& t) { { t.__len__() } -> std::same_as<BigInt>; }
              && !requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; })
int32_t __len__(const T& x) {
    return x.__len__().template to_fixed_check<int32_t>();
}

// =============================================
// tpy::__getitem__
// =============================================

// Overload: std::vector
template<typename T>
decltype(auto) __getitem__(const std::vector<T>& x, int32_t i) {
    auto idx = normalize_index(x, i, "list index out of bounds");
    return x[idx];
}

template<typename T>
decltype(auto) __getitem__(std::vector<T>& x, int32_t i) {
    auto idx = normalize_index(x, i, "list index out of bounds");
    return x[idx];
}

// Overload: std::array
template<typename T, std::size_t N>
decltype(auto) __getitem__(const std::array<T, N>& x, int32_t i) {
    auto idx = normalize_index(x, i, "array index out of bounds");
    return x[idx];
}

template<typename T, std::size_t N>
decltype(auto) __getitem__(std::array<T, N>& x, int32_t i) {
    auto idx = normalize_index(x, i, "array index out of bounds");
    return x[idx];
}

// Overload: std::span (const)
template<typename T>
decltype(auto) __getitem__(std::span<const T> x, int32_t i) {
    auto idx = normalize_index(x, i, "span index out of bounds");
    return x[idx];
}

// Overload: std::span (mutable)
template<typename T>
T& __getitem__(std::span<T> x, int32_t i) {
    auto idx = normalize_index(x, i, "span index out of bounds");
    return x[idx];
}

// Overload: std::string
inline char __getitem__(const std::string& x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of bounds");
    return x[idx];
}

// Overload: std::string_view
inline char __getitem__(std::string_view x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of bounds");
    return x[idx];
}

// Overload: ordered_map (dict) -- key can be any compatible type
template<typename K, typename V, typename KeyArg>
const V& __getitem__(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) tpy_panic("KeyError");
    return (*it).second;
}

template<typename K, typename V, typename KeyArg>
V& __getitem__(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) tpy_panic("KeyError");
    return (*it).second;
}

// Default template: user types that define __getitem__() method
template<typename T>
    requires requires(const T& t, int32_t i) { t.__getitem__(i); }
decltype(auto) __getitem__(const T& x, int32_t i) {
    return x.__getitem__(i);
}

template<typename T>
    requires requires(T& t, int32_t i) { t.__getitem__(i); }
decltype(auto) __getitem__(T& x, int32_t i) {
    return x.__getitem__(i);
}

// =============================================
// tpy::__setitem__
// =============================================

// Overload: std::vector
template<typename T, typename V>
void __setitem__(std::vector<T>& x, int32_t i, V&& v) {
    auto idx = normalize_index(x, i, "list index out of bounds");
    x[idx] = std::forward<V>(v);
}

// Overload: std::array
template<typename T, std::size_t N, typename V>
void __setitem__(std::array<T, N>& x, int32_t i, V&& v) {
    auto idx = normalize_index(x, i, "array index out of bounds");
    x[idx] = std::forward<V>(v);
}

// Overload: std::span (mutable)
template<typename T, typename V>
void __setitem__(std::span<T> x, int32_t i, V&& v) {
    auto idx = normalize_index(x, i, "span index out of bounds");
    x[idx] = std::forward<V>(v);
}

// Overload: ordered_map (dict)
template<typename K, typename V, typename KeyArg, typename ValArg>
void __setitem__(ordered_map<K, V>& m, const KeyArg& key, ValArg&& value) {
    m.insert_or_assign(K(key), V(std::forward<ValArg>(value)));
}

// Default template: user types that define __setitem__() method
template<typename T, typename V>
    requires requires(T& t, int32_t i, V&& val) { t.__setitem__(i, std::forward<V>(val)); }
void __setitem__(T& x, int32_t i, V&& v) {
    x.__setitem__(i, std::forward<V>(v));
}

// =============================================
// tpy::__delitem__
// =============================================

// Overload: std::vector (list)
template<typename T>
void __delitem__(std::vector<T>& x, int32_t i) {
    auto idx = normalize_index(x, i, "list index out of bounds");
    x.erase(x.begin() + static_cast<std::ptrdiff_t>(idx));
}

// Overload: ordered_map (dict) -- panics on missing key
template<typename K, typename V, typename KeyArg>
void __delitem__(ordered_map<K, V>& m, const KeyArg& key) {
    if (!m.erase(K(key))) {
        tpy_panic("KeyError");
    }
}

// Default template: user types that define __delitem__() method
template<typename T>
    requires requires(T& t, int32_t i) { t.__delitem__(i); }
void __delitem__(T& x, int32_t i) {
    x.__delitem__(i);
}

// =============================================
// tpy::__bool__
// =============================================

// Overload: ordered_map (dict)
template<typename K, typename V>
bool __bool__(const ordered_map<K, V>& m) {
    return !m.empty();
}

// Default template: user types that define __bool__() method
template<typename T>
    requires requires(const T& t) { { t.__bool__() } -> std::convertible_to<bool>; }
bool __bool__(const T& x) {
    return x.__bool__();
}

// =============================================
// tpy::__str__
// =============================================

// Builtin type overloads (needed for generic T contexts)
inline std::string __str__(bool x) { return x ? "True" : "False"; }
inline std::string __str__(char x) { return std::string(1, x); }
inline std::string __str__(int8_t x) { return std::to_string(x); }
inline std::string __str__(int16_t x) { return std::to_string(x); }
inline std::string __str__(int32_t x) { return std::to_string(x); }
inline std::string __str__(int64_t x) { return std::to_string(x); }
inline std::string __str__(uint8_t x) { return std::to_string(x); }
inline std::string __str__(uint16_t x) { return std::to_string(x); }
inline std::string __str__(uint32_t x) { return std::to_string(x); }
inline std::string __str__(uint64_t x) { return std::to_string(x); }
inline std::string __str__(double x) { return format_float(x); }
inline std::string __str__(float x) { return format_float(static_cast<double>(x)); }
inline std::string __str__(const std::string& x) { return x; }
inline std::string __str__(std::string_view x) { return std::string(x); }
inline std::string __str__(const BigInt& x) { return x.to_string(); }

// Default template: user types that define __str__() method
template<typename T>
    requires requires(const T& t) { t.__str__(); }
auto __str__(const T& x) {
    return x.__str__();
}

// Fallback: types with __repr__ but no __str__ (matches Python behavior)
template<typename T>
    requires (!requires(const T& t) { t.__str__(); })
          && requires(const T& t) { t.__repr__(); }
auto __str__(const T& x) {
    return x.__repr__();
}

// Fallback for formattable types without __str__/__repr__ (e.g. int, double).
template<typename T>
    requires (!requires(const T& t) { t.__str__(); })
          && (!requires(const T& t) { t.__repr__(); })
          && std::formattable<T, char>
auto __str__(const T& x) {
    return std::format("{}", x);
}

// Fallback for types with operator<< but no __str__/__repr__/formattable.
template<typename T>
    requires (!requires(const T& t) { t.__str__(); })
          && (!requires(const T& t) { t.__repr__(); })
          && (!std::formattable<T, char>)
          && requires(std::ostream& os, const T& t) { os << t; }
std::string __str__(const T& x) {
    std::ostringstream ss;
    ss << x;
    return ss.str();
}

// =============================================
// tpy::__repr__
// =============================================

// Default template: user types that define __repr__() method
template<typename T>
    requires requires(const T& t) { t.__repr__(); }
auto __repr__(const T& x) {
    return x.__repr__();
}

// Strings: Python repr wraps in single quotes
inline std::string __repr__(const std::string& x) {
    return "'" + x + "'";
}
inline std::string __repr__(std::string_view x) {
    return "'" + std::string(x) + "'";
}

// Fallback for formattable types without __repr__ (e.g. int, double).
template<typename T>
    requires (!requires(const T& t) { t.__repr__(); })
          && (!std::same_as<T, std::string>)
          && (!std::same_as<T, std::string_view>)
          && std::formattable<T, char>
auto __repr__(const T& x) {
    return std::format("{}", x);
}

// Fallback for types with operator<< but no __repr__/formattable.
template<typename T>
    requires (!requires(const T& t) { t.__repr__(); })
          && (!std::formattable<T, char>)
          && requires(std::ostream& os, const T& t) { os << t; }
std::string __repr__(const T& x) {
    std::ostringstream ss;
    ss << x;
    return ss.str();
}

// =============================================
// tpy::__hash__
// =============================================

// Integral types (int8_t through uint64_t, bool, char)
template<typename T>
    requires std::integral<T>
uint64_t __hash__(T x) {
    return static_cast<uint64_t>(std::hash<T>{}(x));
}

// Floating-point
inline uint64_t __hash__(double x) {
    return static_cast<uint64_t>(std::hash<double>{}(x));
}

// Strings
inline uint64_t __hash__(const std::string& x) {
    return static_cast<uint64_t>(std::hash<std::string>{}(x));
}
inline uint64_t __hash__(std::string_view x) {
    return static_cast<uint64_t>(std::hash<std::string_view>{}(x));
}
inline uint64_t __hash__(const char* x) {
    return static_cast<uint64_t>(std::hash<std::string_view>{}(std::string_view(x)));
}

// Enum types
template<typename T>
    requires std::is_enum_v<T>
uint64_t __hash__(T x) {
    return static_cast<uint64_t>(std::hash<std::underlying_type_t<T>>{}(
        static_cast<std::underlying_type_t<T>>(x)));
}

// Default: user types with __hash__() method
template<typename T>
    requires requires(const T& t) { { t.__hash__() } -> std::convertible_to<uint64_t>; }
uint64_t __hash__(const T& x) {
    return x.__hash__();
}

// =============================================
// tpy::hash_combine -- Boost-style hash combining for dataclass __hash__
// =============================================

inline uint64_t hash_combine(uint64_t seed) {
    return seed;
}

template<typename T, typename... Rest>
uint64_t hash_combine(uint64_t seed, const T& val, const Rest&... rest) {
    seed ^= __hash__(val) + 0x9e3779b97f4a7c15ULL + (seed << 6) + (seed >> 2);
    return hash_combine(seed, rest...);
}

// =============================================
// native_iterator: wraps C++ begin/end into __next_opt__()
// =============================================

template<typename Iter, typename T>
struct native_iterator {
    Iter current_;
    Iter end_;

    std::optional<T> __next_opt__() {
        if (current_ == end_) return std::nullopt;
        return std::optional<T>{*current_++};
    }

    native_iterator& __iter__() { return *this; }

    // Opaque repr, matching CPython's behavior for iterator objects.
    friend std::ostream& operator<<(std::ostream& os, const native_iterator&) {
        return os << "<iterator>";
    }
};

// =============================================
// tpy::__iter__
// =============================================

// Overload: std::vector
template<typename T>
auto __iter__(const std::vector<T>& x) {
    return native_iterator<typename std::vector<T>::const_iterator, T>{x.begin(), x.end()};
}

// Overload: std::array
template<typename T, std::size_t N>
auto __iter__(const std::array<T, N>& x) {
    return native_iterator<typename std::array<T, N>::const_iterator, T>{x.begin(), x.end()};
}

// Overload: std::span (const)
template<typename T>
auto __iter__(std::span<const T> x) {
    return native_iterator<typename std::span<const T>::iterator, T>{x.begin(), x.end()};
}

// Overload: std::span (mutable)
template<typename T>
auto __iter__(std::span<T> x) {
    return native_iterator<typename std::span<T>::iterator, T>{x.begin(), x.end()};
}

// Overload: std::string
inline auto __iter__(const std::string& x) {
    return native_iterator<std::string::const_iterator, char>{x.begin(), x.end()};
}

// Overload: std::string_view
inline auto __iter__(std::string_view x) {
    return native_iterator<std::string_view::const_iterator, char>{x.begin(), x.end()};
}

// Overload: const char* (string literals)
inline auto __iter__(const char* x) {
    return __iter__(std::string_view(x));
}

// Generic overload: any C++ range type not covered above (e.g., tpy::Range<T>)
// Constrained to types without __iter__() to avoid ambiguity with user types.
template<typename T>
    requires std::ranges::input_range<const T>
          && (!requires(const T& t) { t.__iter__(); })
auto __iter__(const T& x) {
    using ElemT = std::remove_cvref_t<decltype(*x.begin())>;
    return native_iterator<decltype(x.begin()), ElemT>{x.begin(), x.end()};
}

// Default template: user types that define __iter__() method
template<typename T>
    requires requires(T& t) { t.__iter__(); }
auto __iter__(T& x) {
    return x.__iter__();
}

// Const overload for user types
template<typename T>
    requires requires(const T& t) { t.__iter__(); }
auto __iter__(const T& x) {
    return x.__iter__();
}

} // namespace tpy
