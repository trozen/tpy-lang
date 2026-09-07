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

#include "lookup_key.hpp"
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
    // CPython validates the length slot's result for every consumer (len(),
    // truthiness, reversed(), ...), not just for len(); guard here so all share it.
    int32_t n = x.__len__();
    if (n < 0) raise_value_error("__len__() should return >= 0");
    return n;
}

// User types returning BigInt from __len__()
template<typename T>
    requires (requires(const T& t) { { t.__len__() } -> std::same_as<BigInt>; }
              && !requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; })
int32_t __len__(const T& x) {
    // to_fixed_check narrows the BigInt (and panics on an oversized result); a
    // negative in-range result is still ValueError, like the int-convertible branch.
    int32_t n = x.__len__().template to_fixed_check<int32_t>();
    if (n < 0) raise_value_error("__len__() should return >= 0");
    return n;
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

// Overload: ordered_map (dict) -- the key is forwarded in the form it arrives
// in; ordered_map::find decides whether it can probe the table directly.
template<typename K, typename V, typename KeyArg>
const V& __getitem__(const ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(key);
    if (it == m.items_end()) raise_key_error("KeyError");
    return (*it).second;
}

template<typename K, typename V, typename KeyArg>
V& __getitem__(ordered_map<K, V>& m, const KeyArg& key) {
    auto it = m.find(key);
    if (it == m.items_end()) raise_key_error("KeyError");
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
    // K(key) avoids double-conversion (find takes const K&); value is
    // forwarded raw into insert_or_assign's universal-ref VV.
    m.insert_or_assign(K(key), std::forward<ValArg>(value));
}

// Default template: user types that define __setitem__() method. The key is a
// deduced K (not just int32_t) so a non-int key (e.g. a str-keyed mapping)
// dispatches to the user method; the `requires` gate keeps non-mapping types
// out, and the concrete container overloads above stay more-specialized.
template<typename T, typename K, typename V>
    requires requires(T& t, K&& k, V&& val) { t.__setitem__(std::forward<K>(k), std::forward<V>(val)); }
void __setitem__(T& x, K&& k, V&& v) {
    x.__setitem__(std::forward<K>(k), std::forward<V>(v));
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
    if (!m.erase(key)) {
        raise_key_error("KeyError");
    }
}

// Default template: user types that define __delitem__() method. Deduced key K
// (not just int32_t) so a non-int key dispatches to the user method.
template<typename T, typename K>
    requires requires(T& t, K&& k) { t.__delitem__(std::forward<K>(k)); }
void __delitem__(T& x, K&& k) {
    x.__delitem__(std::forward<K>(k));
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

// Pointer-repr Optional truthiness (Python `if p:` on a `Record | None`):
// null is falsy, otherwise the pointee's Python truthiness. Kept as a single
// evaluation of `p` so it is correct in loop conditions and side-effecting
// operands, where a hoisted null-check-plus-deref temp would go stale. The
// dispatch order mirrors codegen's record truthiness: __bool__, else
// __len__ != 0, else the object-default (a non-None instance is truthy).
template<typename T>
inline bool ptr_truthy(const T* p) {
    if (p == nullptr) return false;
    if constexpr (requires { { p->__bool__() } -> std::convertible_to<bool>; }) {
        return p->__bool__();
    } else if constexpr (requires { p->__len__(); }) {
        return ::tpy::__len__(*p) != 0;
    } else {
        return true;
    }
}

// =============================================
// tpy::__str__
// =============================================

// Builtin type overloads (needed for generic T contexts)
inline std::string __str__(bool x) { return x ? "True" : "False"; }
inline std::string __str__(std::nullptr_t) { return "None"; }
inline std::string __str__(std::monostate) { return "None"; }
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

// std::monostate is the TPy unit type at value-bearing positions
// (Future[None], list[None], ...). Mirror the nullptr_t overload so
// any_repr_capable<monostate> matches and Any cells holding None
// route to "None" rather than the typeid fallback.
inline std::string_view __repr__(std::monostate) {
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

// std::monostate is the TPy unit type at value-bearing positions; map
// to Python's hash(None) (== 0 on most CPython builds, but the only
// requirement is stability + matching None==None equality).
inline uint64_t __hash__(std::monostate) {
    return 0;
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

// A `T*` value is a tuple / Optional borrow slot, not a raw address: hash the
// referent (a null pointer-repr Optional slot hashes as None). char* keeps its
// dedicated overload above. Defined after the user-type __hash__ templates so
// the dependent `__hash__(*x)` call resolves to them at instantiation.
template<typename T>
    requires (!std::is_same_v<std::remove_cv_t<T>, char>)
uint64_t __hash__(const T* x) {
    return x == nullptr ? __hash__(std::monostate{}) : __hash__(*x);
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

// Iterator-protocol type helpers for spelling resumable for-loop frame fields
// (the iterator slot and the per-step result slot) without the nested decltype
// formulas inline. Defined after every free `__iter__` overload so the
// qualified `::tpy::__iter__` lookup inside `iter_type_t` sees the full set.
template<typename S>
using iter_type_t = std::decay_t<decltype(::tpy::__iter__(std::declval<S&>()))>;
template<typename It>
using iter_next_t = decltype(std::declval<It&>().__next__());
template<typename S>
using iter_result_t = iter_next_t<iter_type_t<S>>;
template<typename C>
using begin_iter_t = decltype(std::declval<C&>().begin());
template<typename S>
using aiter_type_t = std::decay_t<decltype(std::declval<S&>().__aiter__())>;

// Storage form for a resumable frame's for-loop VARIABLE. A non-value element
// the source merely lends must alias it (`T&` -> frame_slot stores `T*`), so
// mutation through the loop var reaches the source as CPython requires; a fresh
// value must be owned or the field dangles once the step that produced it ends.
// Which one applies is frequently not knowable where TPy declares the field --
// a protocol-typed or generic source can be instantiated either way -- so the
// two traits below decide it here, from the source's own iteration protocol.
//
// The rules differ per protocol because what proves "borrow" differs, and each
// is unambiguous only within its own protocol:
//   *it       -- an lvalue reference IS an element of the container. Trustworthy.
//   __next__  -- a `val_or_ref` result is the borrow marker. A bare `T&` proves
//                nothing: unwrap_ref yields `T&` for a fresh value too, since
//                that value lives in the caller's result slot.
namespace detail {
    template<typename E>
    struct elem_form {
        using bare = std::remove_reference_t<E>;
        // Only an lvalue reference to a non-value type is worth aliasing;
        // const-ness is preserved so a readonly element binds as `const T*`.
        static constexpr bool alias =
            std::is_lvalue_reference_v<E>
            && !is_value_type_v<std::remove_cv_t<bare>>;
        using type = std::conditional_t<alias, bare&, std::remove_cv_t<bare>>;
    };

    template<typename R>
    struct next_elem_form {
        // Not a val_or_ref: a fresh value, so own it.
        using type = std::remove_cvref_t<R>;
    };
    template<typename R>
        requires requires { typename std::remove_cvref_t<R>::is_val_or_ref_tag; }
    struct next_elem_form<R> {
        using type = typename elem_form<decltype(
            std::declval<const std::remove_cvref_t<R>&>().get())>::type;
    };
}

// begin()/end() sources (containers): the element form of `*it`.
template<typename It>
using for_elem_deref_t =
    typename detail::elem_form<decltype(*std::declval<It&>())>::type;

// __iter__()/__next__() sources (protocol params, user iterables, generators):
// the element form of the step result's payload.
template<typename S>
using for_elem_next_t =
    typename detail::next_elem_form<typename iter_result_t<S>::value_type>::type;

// `with CM() as t` targets: the element form of `__enter__()`'s result. A
// manager that lends (`return self`) yields `T&`, so the frame slot aliases it
// and a mutation through the target reaches the object __exit__ runs against; a
// manager that builds a fresh value yields `T`, so the slot owns. Same
// undecidable-in-the-compiler question as a for-loop element, so it takes the
// same answer: spell the field from the source and let C++ pick at
// instantiation.
template<typename CM>
using with_enter_t =
    typename detail::elem_form<decltype(std::declval<CM&>().__enter__())>::type;

} // namespace tpy


