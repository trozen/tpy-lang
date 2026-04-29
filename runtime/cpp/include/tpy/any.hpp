/**
 * TurboPython Runtime - Any (typing.Any)
 *
 * Type-erased value cell. Holds any concrete copyable value via std::any
 * plus a per-type ops table for the universal operations (print, str,
 * repr, bool, equals, hash) that the compiler dispatches on raw `Any`.
 *
 * v1 stores copyable contents only -- the std::any backing requires
 * CopyConstructible. Move-only support is deferred (would require a
 * custom wrapper with destroy/move slots replacing std::any).
 *
 * See docs/ANY_TYPE_DESIGN.md for the full design.
 *
 * Depends on: dunder.hpp (__str__, __repr__, __hash__), builtins.hpp
 * (to_bool), core.hpp (tpy_panic).
 */

#pragma once

#include <any>
#include <concepts>
#include <cstdint>
#include <format>
#include <ostream>
#include <string>
#include <string_view>
#include <type_traits>
#include <typeinfo>
#include <utility>

namespace tpy {

// Forward declarations from other tpy headers; these resolve via ADL when
// any.hpp is included after dunder.hpp / builtins.hpp / core.hpp via the
// umbrella tpy.hpp. We do not include them directly to keep this header
// self-contained when read in isolation.

struct AnyOps;

// ---------------------------------------------------------------------
// Any: std::any storage + per-type ops table
// ---------------------------------------------------------------------

struct Any {
    std::any value;
    const AnyOps* ops;

    // Default-constructed Any is the empty/moved-from state. TPy source
    // does not allow this (no default-init for non-trivial types); it
    // arises only from std::any moves. Operations on an empty Any panic
    // -- see any_cast_or_panic / any_eq / any_hash below.
    Any() noexcept : value(), ops(nullptr) {}

    Any(std::any v, const AnyOps* o) noexcept
        : value(std::move(v)), ops(o) {}

    bool empty() const noexcept { return !value.has_value(); }
};

// ---------------------------------------------------------------------
// AnyOps: dispatch table for universal operations
// ---------------------------------------------------------------------
//
// `equals` and `hash` are populated only for types that satisfy the
// corresponding compile-time concept (operator== / tpy::__hash__). When
// null, the runtime path panics or returns False per the design doc.

struct AnyOps {
    void (*print)(std::ostream&, const std::any&);
    void (*str)(std::string&, const std::any&);
    void (*repr)(std::string&, const std::any&);
    bool (*to_bool)(const std::any&);
    bool (*equals)(const std::any&, const std::any&);   // null if T not Eq
    std::uint64_t (*hash)(const std::any&);             // null if T not Hashable
};

// ---------------------------------------------------------------------
// Per-T conditional slot generation
// ---------------------------------------------------------------------

namespace detail {

template <typename T>
concept any_str_capable = requires(const T& t) {
    { ::tpy::__str__(t) } -> std::convertible_to<std::string>;
};

template <typename T>
concept any_repr_capable = requires(const T& t) {
    { ::tpy::__repr__(t) } -> std::convertible_to<std::string>;
};

// Mirror tpy::to_bool's acceptance criteria. Going through `tpy::to_bool`
// itself would instantiate its `static_assert(false, ...)` for
// non-capable T at concept-check time and break compilation.
template <typename T>
concept any_to_bool_capable =
    std::is_constructible_v<bool, T> ||
    requires(const T& t) { { t.empty() } -> std::convertible_to<bool>; } ||
    requires(const T& t) { { t.__bool__() } -> std::convertible_to<bool>; };

template <typename T>
concept any_eq_capable = requires(const T& a, const T& b) {
    { a == b } -> std::convertible_to<bool>;
};

template <typename T>
concept any_hash_capable = requires(const T& t) {
    { ::tpy::__hash__(t) } -> std::convertible_to<std::uint64_t>;
};

// Mangled typeid name as the fallback for types without __str__/__repr__.
// Python's default is `<ClassName object at 0x...>`; TPy has no class-name
// registry, so the typeid's mangled name is the best we can do.
inline std::string fallback_object_repr(const std::any& a) {
    std::string s = "<";
    s += a.type().name();
    s += " object>";
    return s;
}

// Resolve the per-T value of type T from std::any once, then pick a
// rendering: prefer_repr=false favours __str__ over __repr__ (str/print
// slots); prefer_repr=true inverts (repr slot). Sink can be either
// std::string& (append) or std::ostream& (operator<<); _emit_to is
// overloaded for both.
inline void _emit_to(std::string& buf, std::string_view s) { buf.append(s); }
inline void _emit_to(std::ostream& os, std::string_view s) { os << s; }

template <typename T, bool prefer_repr, typename Sink>
inline void _write_str_or_repr(Sink& sink, const std::any& a) {
    if constexpr (prefer_repr) {
        if constexpr (any_repr_capable<T>) {
            _emit_to(sink, ::tpy::__repr__(std::any_cast<const T&>(a)));
            return;
        } else if constexpr (any_str_capable<T>) {
            _emit_to(sink, ::tpy::__str__(std::any_cast<const T&>(a)));
            return;
        }
    } else {
        if constexpr (any_str_capable<T>) {
            _emit_to(sink, ::tpy::__str__(std::any_cast<const T&>(a)));
            return;
        } else if constexpr (any_repr_capable<T>) {
            _emit_to(sink, ::tpy::__repr__(std::any_cast<const T&>(a)));
            return;
        }
    }
    _emit_to(sink, fallback_object_repr(a));
}

template <typename T>
constexpr void (*str_slot_for() noexcept)(std::string&, const std::any&) {
    return +[](std::string& buf, const std::any& a) {
        _write_str_or_repr<T, /*prefer_repr=*/false>(buf, a);
    };
}

template <typename T>
constexpr void (*print_slot_for() noexcept)(std::ostream&, const std::any&) {
    return +[](std::ostream& os, const std::any& a) {
        _write_str_or_repr<T, /*prefer_repr=*/false>(os, a);
    };
}

template <typename T>
constexpr void (*repr_slot_for() noexcept)(std::string&, const std::any&) {
    return +[](std::string& buf, const std::any& a) {
        _write_str_or_repr<T, /*prefer_repr=*/true>(buf, a);
    };
}

template <typename T>
constexpr bool (*to_bool_slot_for() noexcept)(const std::any&) {
    if constexpr (any_to_bool_capable<T>) {
        return +[](const std::any& a) -> bool {
            return ::tpy::to_bool(std::any_cast<const T&>(a));
        };
    } else {
        // Python default: objects without __bool__/__len__ are truthy.
        return +[](const std::any&) -> bool { return true; };
    }
}

template <typename T>
constexpr bool (*equals_slot_for() noexcept)(const std::any&, const std::any&) {
    if constexpr (any_eq_capable<T>) {
        return +[](const std::any& a, const std::any& b) -> bool {
            return std::any_cast<const T&>(a) == std::any_cast<const T&>(b);
        };
    } else {
        return nullptr;
    }
}

template <typename T>
constexpr std::uint64_t (*hash_slot_for() noexcept)(const std::any&) {
    if constexpr (any_hash_capable<T>) {
        return +[](const std::any& a) -> std::uint64_t {
            return ::tpy::__hash__(std::any_cast<const T&>(a));
        };
    } else {
        return nullptr;
    }
}

}  // namespace detail

// `inline constexpr` template variables guarantee a single instance per
// T across translation units in a single binary. Cross-shared-library
// identity is out of scope (TPy builds as one binary).
//
// Every slot is conditionally generated. Slots whose underlying op is
// missing fall back to a sensible default (Python-conformant where
// possible): str/print/repr render `<typeid object>`, to_bool returns
// true, equals/hash stay null and trigger the documented runtime
// behaviour (== returns False; hash() panics).
template <typename T>
inline constexpr AnyOps any_ops_for = {
    /*print=*/detail::print_slot_for<T>(),
    /*str=*/detail::str_slot_for<T>(),
    /*repr=*/detail::repr_slot_for<T>(),
    /*to_bool=*/detail::to_bool_slot_for<T>(),
    /*equals=*/detail::equals_slot_for<T>(),
    /*hash=*/detail::hash_slot_for<T>(),
};

// ---------------------------------------------------------------------
// make_any<T>: factory that wires the value to its per-T ops table
// ---------------------------------------------------------------------
//
// Codegen emits `::tpy::make_any(value)` and lets argument deduction
// pick T -- so the source is `make_any(BigInt(42))` rather than the
// hand-spelled `Any{std::any{BigInt(42)}, &any_ops_for<BigInt>}`.
// Callers must build `value` so its decayed C++ type *is* the desired
// storage typeid (e.g. `BigInt(42)` not `42`, `std::string("hi")` not
// `"hi"`); the codegen layer's _any_storage_form ensures that.

template <typename T>
inline Any make_any(T value) {
    return Any{std::any{std::move(value)}, &any_ops_for<T>};
}

// ---------------------------------------------------------------------
// any_cast_or_panic<T>: checked extraction
// ---------------------------------------------------------------------
//
// const-ref overload copies the contents out (auto-coerce paths use
// this). The rvalue overload moves out at last use.

template <typename T>
T any_cast_or_panic(const Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    if (a.value.type() != typeid(T)) {
        ::tpy::tpy_panic(std::format(
            "Any: expected {}, got {}",
            typeid(T).name(), a.value.type().name()));
    }
    return std::any_cast<T>(a.value);
}

template <typename T>
T any_cast_or_panic(Any&& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    if (a.value.type() != typeid(T)) {
        ::tpy::tpy_panic(std::format(
            "Any: expected {}, got {}",
            typeid(T).name(), a.value.type().name()));
    }
    T moved = std::any_cast<T>(std::move(a.value));
    // Clear the source so the documented "use of empty/moved-from Any"
    // panic fires on subsequent access. std::any_cast<T>(rvalue) does
    // not necessarily empty the source.
    a.value.reset();
    a.ops = nullptr;
    return moved;
}

// ---------------------------------------------------------------------
// any_eq / any_eq_concrete / any_hash
// ---------------------------------------------------------------------
//
// `==` semantics: type-mismatch returns False (Python behaviour for
// non-promotable types). When the equals slot is null (T isn't Eq),
// also False -- the only honest answer when T has no notion of equality.
// Empty/moved-from Any is never equal to anything (also returns False).

inline bool any_eq(const Any& a, const Any& b) {
    if (a.empty() || b.empty()) return false;
    if (a.value.type() != b.value.type()) return false;
    if (a.ops == nullptr || a.ops->equals == nullptr) return false;
    return a.ops->equals(a.value, b.value);
}

template <typename U>
bool any_eq_concrete(const Any& a, const U& b) {
    if (a.empty()) return false;
    if (a.value.type() != typeid(U)) return false;
    return std::any_cast<const U&>(a.value) == b;
}

template <typename U>
bool any_eq_concrete(const U& a, const Any& b) {
    return any_eq_concrete<U>(b, a);
}

// Hashing: panics if the contained T is not Hashable, or if the cell
// is empty. Compile-time `hash(any_var)` cannot fail at type check
// because Any satisfies the Hashable concept at the type-system level
// -- the runtime check is the trade-off for accepting arbitrary T.
inline std::uint64_t any_hash(const Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    if (a.ops == nullptr || a.ops->hash == nullptr) {
        ::tpy::tpy_panic(std::format(
            "cannot hash Any holding {} -- type is not Hashable",
            a.value.type().name()));
    }
    return a.ops->hash(a.value);
}

// Container-element printing routes through `tpy::detail::print_element`,
// which renders strings as `'foo'` (repr-flavoured) so `print(d)` for
// `dict[str, str]` reads `{'k': 'v'}`. Any values inside containers
// follow the same convention -- route through the repr slot rather
// than the str slot used by top-level operator<<.
namespace detail {
inline void print_element(std::ostream& os, const ::tpy::Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    std::string buf;
    a.ops->repr(buf, a.value);
    os << buf;
}
}  // namespace detail

// ---------------------------------------------------------------------
// Integration with TPy runtime primitives
// ---------------------------------------------------------------------
//
// `__str__(Any)` falls through to `operator<<` via dunder.hpp's
// generic-fallback chain -- defining `operator<<` is enough.
// `__repr__(Any)` needs an explicit definition because the fallback
// would route through `operator<<` (which renders via the str slot,
// not the repr slot) and silently produce the str representation.

inline std::ostream& operator<<(std::ostream& os, const Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    a.ops->print(os, a.value);
    return os;
}

inline std::string __repr__(const Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    std::string buf;
    a.ops->repr(buf, a.value);
    return buf;
}

inline std::uint64_t __hash__(const Any& a) {
    return any_hash(a);
}

// `to_bool(const T&)` in builtins.hpp tests `requires { x.empty(); }`
// before `__bool__()`, so Any's empty() helper would otherwise hijack
// truthiness. This explicit overload routes through the to_bool slot.
inline bool to_bool(const Any& a) {
    if (a.empty()) {
        ::tpy::tpy_panic("use of empty/moved-from Any");
    }
    return a.ops->to_bool(a.value);
}

// Equality: typeid-checked. Type mismatch returns False (Python
// behaviour for incomparable types); null equals slot also False.

inline bool operator==(const Any& a, const Any& b) {
    return any_eq(a, b);
}

template <typename U>
    requires (!std::same_as<U, Any>)
inline bool operator==(const Any& a, const U& b) {
    return any_eq_concrete<U>(a, b);
}

template <typename U>
    requires (!std::same_as<U, Any>)
inline bool operator==(const U& a, const Any& b) {
    return any_eq_concrete<U>(b, a);
}

}  // namespace tpy

// std::hash<tpy::Any> for use as a key in std::unordered_map / set
// based containers (TPy's ordered_map / ordered_set). Routes through
// the per-type hash slot; panics if the contained T isn't Hashable.
template <>
struct std::hash<::tpy::Any> {
    std::size_t operator()(const ::tpy::Any& a) const {
        return static_cast<std::size_t>(::tpy::any_hash(a));
    }
};

// std::format / f-string support: route through the str slot. Format
// specs are not currently supported on Any (the design defers richer
// dispatch); only `{}` is accepted.
template <>
struct std::formatter<::tpy::Any> {
    constexpr auto parse(std::format_parse_context& ctx) {
        auto it = ctx.begin();
        if (it != ctx.end() && *it != '}') {
            throw std::format_error(
                "format specs on Any are not supported -- narrow first");
        }
        return it;
    }

    auto format(const ::tpy::Any& a, std::format_context& ctx) const {
        if (a.empty()) {
            ::tpy::tpy_panic("use of empty/moved-from Any");
        }
        std::string buf;
        a.ops->str(buf, a.value);
        return std::format_to(ctx.out(), "{}", buf);
    }
};
