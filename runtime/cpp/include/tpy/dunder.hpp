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
#include <cstring>
#include <expected>
#include <format>
#include <functional>
#include <ranges>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

#include "bigint.hpp"
#include "enum.hpp"
#include "format.hpp"
#include "container_ops.hpp"
#include "next_iter.hpp"
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

// Overload: char (Char type -- always length 1)
inline int32_t __len__(char) {
    return 1;
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
    auto idx = normalize_index(x, i, "list index out of range");
    return x[idx];
}

template<typename T>
decltype(auto) __getitem__(std::vector<T>& x, int32_t i) {
    auto idx = normalize_index(x, i, "list index out of range");
    return x[idx];
}

// Overload: std::array
template<typename T, std::size_t N>
decltype(auto) __getitem__(const std::array<T, N>& x, int32_t i) {
    auto idx = normalize_index(x, i, "array index out of range");
    return x[idx];
}

template<typename T, std::size_t N>
decltype(auto) __getitem__(std::array<T, N>& x, int32_t i) {
    auto idx = normalize_index(x, i, "array index out of range");
    return x[idx];
}

// Overload: std::span (const)
template<typename T>
decltype(auto) __getitem__(std::span<const T> x, int32_t i) {
    auto idx = normalize_index(x, i, "span index out of range");
    return x[idx];
}

// Overload: std::span (mutable)
template<typename T>
T& __getitem__(std::span<T> x, int32_t i) {
    auto idx = normalize_index(x, i, "span index out of range");
    return x[idx];
}

// Overload: std::string
inline char __getitem__(const std::string& x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of range");
    return x[idx];
}

// Overload: std::string_view
inline char __getitem__(std::string_view x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of range");
    return x[idx];
}

// Overload: ordered_map (dict) -- key can be any compatible type
template<typename K, typename V, typename KeyArg>
const V& __getitem__(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) raise<KeyError>("KeyError");
    return (*it).second;
}

template<typename K, typename V, typename KeyArg>
V& __getitem__(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(K(key));
    if (it == m.items_end()) raise<KeyError>("KeyError");
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
    auto idx = normalize_index(x, i, "list assignment index out of range");
    x[idx] = std::forward<V>(v);
}

// Overload: std::array
template<typename T, std::size_t N, typename V>
void __setitem__(std::array<T, N>& x, int32_t i, V&& v) {
    auto idx = normalize_index(x, i, "array index out of range");
    x[idx] = std::forward<V>(v);
}

// Overload: std::span (mutable)
template<typename T, typename V>
void __setitem__(std::span<T> x, int32_t i, V&& v) {
    auto idx = normalize_index(x, i, "span index out of range");
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
    auto idx = normalize_index(x, i, "list assignment index out of range");
    x.erase(x.begin() + static_cast<std::ptrdiff_t>(idx));
}

// Overload: ordered_map (dict) -- throws KeyError on missing key
template<typename K, typename V, typename KeyArg>
void __delitem__(ordered_map<K, V>& m, const KeyArg& key) {
    if (!m.erase(K(key))) {
        raise<KeyError>("KeyError");
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
inline std::string __str__(std::nullptr_t) { return "None"; }
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

// Enum str: same shape as enum __repr__ above. Python prints str(e) and
// repr(e) identically for enums ("TypeName.MEMBER"), so delegate.
template<typename T>
    requires std::is_enum_v<T>
          && requires(T x) { ::tpy::EnumUtil<T>::name(x); }
inline std::string __str__(T x) {
    return ::tpy::detail::enum_repr_string(x);
}

// Fallback for types with operator<< but no __str__/__repr__/formattable.
// Excludes enums for the same reason as the __repr__ fallback above.
template<typename T>
    requires (!requires(const T& t) { t.__str__(); })
          && (!requires(const T& t) { t.__repr__(); })
          && (!std::is_enum_v<T>)
          && (!std::formattable<T, char>)
          && requires(std::ostream& os, const T& t) { os << t; }
std::string __str__(const T& x) {
    std::ostringstream ss;
    ss << x;
    return ss.str();
}

// Union (std::variant): visit the active alternative and recurse. Pointer
// alternatives (T*) are dereffed before dispatch (matches the variant
// repr overload below).
template<typename... Ts>
std::string __str__(const std::variant<Ts...>& v) {
    return std::visit([](auto&& a) -> std::string {
        using A = std::remove_cvref_t<decltype(a)>;
        if constexpr (std::is_pointer_v<A>) {
            return std::string(::tpy::__str__(*a));
        } else {
            return std::string(::tpy::__str__(a));
        }
    }, v);
}

// =============================================
// tpy::__repr__
// =============================================

// Forward decl: repr_of is the user-facing dispatch helper, defined in
// tpy.hpp after every header that contributes __repr__ overloads (notably
// bytes_ops.hpp which adds __repr__ for raw Bytes). Optional/variant repr
// templates above use it for ADL into per-record __repr__ overrides;
// qualified lookup of `::tpy::repr_of` from a template body needs the
// name visible at parse time, so the forward declaration precedes those
// template bodies.
template<typename T> auto repr_of(const T& x);

// Default template: user types that define __repr__() method
template<typename T>
    requires requires(const T& t) { t.__repr__(); }
auto __repr__(const T& x) {
    return x.__repr__();
}

// User records without __repr__: emit the Python default
// `<ClassName object at 0xADDR>` form. The class name comes from a
// `static constexpr __tpy_class_name__` member emitted by codegen.
template<typename T>
    requires (!requires(const T& t) { t.__repr__(); })
          && requires { T::__tpy_class_name__; }
inline std::string __repr__(const T& x) {
    std::ostringstream ss;
    ::tpy::print_object_default(ss, T::__tpy_class_name__, x);
    return ss.str();
}

// Bool: Python repr uses True/False (not C++ true/false)
inline std::string_view __repr__(bool x) {
    return x ? "True" : "False";
}

// None: repr matches str ("None")
inline std::string_view __repr__(std::nullptr_t) {
    return "None";
}

// Strings: Python repr quotes and escapes control chars / quote / backslash.
// The const char* overload steals string-literal calls before the templated
// formattable fallback (which would otherwise emit raw bytes via std::format).
inline std::string __repr__(const std::string& x) {
    return repr_quote_string(x);
}
inline std::string __repr__(std::string_view x) {
    return repr_quote_string(x);
}
inline std::string __repr__(const char* x) {
    return repr_quote_string(std::string_view(x));
}

// Optional: None or repr(value).
template<typename T>
std::string __repr__(const std::optional<T>& x) {
    if (!x.has_value()) return "None";
    if constexpr (std::same_as<T, std::string> || std::same_as<T, std::string_view>) {
        return repr_quote_string(*x);
    } else if constexpr (std::same_as<T, bool>) {
        return *x ? "True" : "False";
    } else if constexpr (std::floating_point<T>) {
        return format_float(static_cast<double>(*x));
    } else {
        // repr_of (not direct ::tpy::__repr__) so ADL into T's namespace
        // can reach per-record overrides; a qualified call from a runtime
        // template body would freeze the candidate set at definition.
        return std::string(::tpy::repr_of(*x));
    }
}

// Python-faithful repr for floating point: always shows trailing .0
inline std::string __repr__(double x) { return format_float(x); }
inline std::string __repr__(float x) { return format_float(static_cast<double>(x)); }

// Fallback for formattable types without __repr__ (e.g. int).
template<typename T>
    requires (!requires(const T& t) { t.__repr__(); })
          && (!std::same_as<T, std::string>)
          && (!std::same_as<T, std::string_view>)
          && (!std::floating_point<T>)
          && std::formattable<T, char>
auto __repr__(const T& x) {
    return std::format("{}", x);
}

// Enum repr: any enum type with an EnumUtil specialization renders as
// "TypeName.MEMBER". The operator<<-fallback below explicitly excludes
// enums via `!std::is_enum_v<T>`, so this template is the only candidate
// for enum types (partitioning, not partial ordering -- the constraint
// sets are disjoint, not subsumption-related). For @native enums there
// is no operator<< at all, so this is the only repr path; for tpy-defined
// enums it produces the same "TypeName.MEMBER" output that the operator<<
// body would have generated.
template<typename T>
    requires std::is_enum_v<T>
          && requires(T x) { ::tpy::EnumUtil<T>::name(x); }
inline std::string __repr__(T x) {
    return ::tpy::detail::enum_repr_string(x);
}

// Fallback for types with operator<< but no __repr__/formattable.
// Excludes TPy records (`__tpy_class_name__`-tagged) so the default-repr
// template above wins unambiguously -- otherwise both templates match
// for records without __repr__. Also excludes enums so the
// EnumUtil-driven template above wins for enum types (constraint sets
// don't subsume, so we disambiguate by partitioning).
template<typename T>
    requires (!requires(const T& t) { t.__repr__(); })
          && (!requires { T::__tpy_class_name__; })
          && (!std::is_enum_v<T>)
          && (!std::formattable<T, char>)
          && requires(std::ostream& os, const T& t) { os << t; }
std::string __repr__(const T& x) {
    std::ostringstream ss;
    ss << x;
    return ss.str();
}

// Union (std::variant): visit the active alternative and recurse. Each
// alternative type must have its own __repr__ overload reachable above;
// pointer-variant alternatives (T*) are dereffed before dispatch. Goes
// through repr_of so ADL on the alternative type finds per-record
// overrides emitted alongside user records.
template<typename... Ts>
std::string __repr__(const std::variant<Ts...>& v) {
    return std::visit([](auto&& a) -> std::string {
        using A = std::remove_cvref_t<decltype(a)>;
        if constexpr (std::is_pointer_v<A>) {
            return std::string(::tpy::repr_of(*a));
        } else {
            return std::string(::tpy::repr_of(a));
        }
    }, v);
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

// Bytes (std::vector<uint8_t>) and BytesView (std::span<const uint8_t>)
inline uint64_t __hash__(const std::vector<uint8_t>& x) {
    return static_cast<uint64_t>(std::hash<std::string_view>{}(
        std::string_view(reinterpret_cast<const char*>(x.data()), x.size())));
}
inline uint64_t __hash__(std::span<const uint8_t> x) {
    return static_cast<uint64_t>(std::hash<std::string_view>{}(
        std::string_view(reinterpret_cast<const char*>(x.data()), x.size())));
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
    return static_cast<uint64_t>(x.__hash__());
}

// User types with __hash__() returning BigInt
template<typename T>
    requires (requires(const T& t) { { t.__hash__() } -> std::same_as<BigInt>; }
              && !requires(const T& t) { { t.__hash__() } -> std::convertible_to<uint64_t>; })
uint64_t __hash__(const T& x) {
    return x.__hash__().hash();
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

// Tuple types
template<typename... Ts>
uint64_t __hash__(const std::tuple<Ts...>& t) {
    return std::apply([](const auto&... elems) {
        return hash_combine(0, elems...);
    }, t);
}

// =============================================
// native_iterator: wraps C++ begin/end into __next__() / __iter__()
// Inherits begin()/end() from next_iter_mixin so the result of
// tpy::__iter__() can also be used in C++ range-for loops.
//
// __next__() returns val_or_ref<deref_type> to preserve reference
// semantics: value types are copied, non-value types are returned
// as pointers (transparent via .get()). This allows protocol-typed
// iteration (Iterable[T], Iterator[T]) to mutate container elements.
// =============================================

template<typename Iter, typename T>
struct native_iterator
    : next_iter_mixin<native_iterator<Iter, T>, val_or_ref<std::remove_reference_t<decltype(*std::declval<Iter&>())>>> {
    using deref_type = std::remove_reference_t<decltype(*std::declval<Iter&>())>;
    using next_type = val_or_ref<deref_type>;

    Iter current_;
    Iter end_;

    native_iterator(Iter begin, Iter end) : current_(begin), end_(end) {}

    std::expected<next_type, StopIteration> __next__() {
        if (current_ == end_) return tpy::make_unexpected(StopIteration{});
        return next_type(*current_++);
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

// Overload: std::vector (mutable -- preserves element references)
template<typename T>
auto __iter__(std::vector<T>& x) {
    return native_iterator<typename std::vector<T>::iterator, T>{x.begin(), x.end()};
}

// Overload: std::vector (const)
template<typename T>
auto __iter__(const std::vector<T>& x) {
    return native_iterator<typename std::vector<T>::const_iterator, T>{x.begin(), x.end()};
}

// Overload: std::array (mutable)
template<typename T, std::size_t N>
auto __iter__(std::array<T, N>& x) {
    return native_iterator<typename std::array<T, N>::iterator, T>{x.begin(), x.end()};
}

// Overload: std::array (const)
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
// decltype(auto) preserves reference returns (e.g. OwnIterSet& __iter__())
// to avoid copying move-only iterator types.
template<typename T>
    requires requires(T& t) { t.__iter__(); }
decltype(auto) __iter__(T& x) {
    return x.__iter__();
}

// Const overload for user types
template<typename T>
    requires requires(const T& t) { t.__iter__(); }
decltype(auto) __iter__(const T& x) {
    return x.__iter__();
}

} // namespace tpy

// std::hash specialization for bytes (needed by std::unordered_map/set)
template<>
struct std::hash<std::vector<uint8_t>> {
    size_t operator()(const std::vector<uint8_t>& v) const noexcept {
        return std::hash<std::string_view>{}(
            std::string_view(reinterpret_cast<const char*>(v.data()), v.size()));
    }
};

// std::hash + std::equal_to specializations for BytesView (std::span<const uint8_t>).
// std::span has no built-in operator==, so unordered_map/set need an explicit
// equality comparator alongside the hash.
template<>
struct std::hash<std::span<const uint8_t>> {
    size_t operator()(std::span<const uint8_t> v) const noexcept {
        // Empty span may hold (nullptr, 0); string_view's hash on a null
        // pointer is implementation-defined. Match equal_to's empty short-circuit.
        if (v.empty()) return 0;
        return std::hash<std::string_view>{}(
            std::string_view(reinterpret_cast<const char*>(v.data()), v.size()));
    }
};
template<>
struct std::equal_to<std::span<const uint8_t>> {
    bool operator()(std::span<const uint8_t> a, std::span<const uint8_t> b) const noexcept {
        if (a.size() != b.size()) return false;
        // memcmp on null pointers is UB even with size 0; std::span is
        // allowed to hold (nullptr, 0) for an empty span.
        return a.empty() || std::memcmp(a.data(), b.data(), a.size()) == 0;
    }
};

// std::hash specialization for tuples (needed by std::unordered_map/set)
template<typename... Ts>
struct std::hash<std::tuple<Ts...>> {
    size_t operator()(const std::tuple<Ts...>& t) const noexcept {
        return static_cast<size_t>(tpy::__hash__(t));
    }
};
