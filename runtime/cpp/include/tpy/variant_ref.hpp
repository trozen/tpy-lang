/**
 * Pointer-variant utilities for non-value union types.
 *
 * Non-value unions (e.g. Dog | Cat where members are records) use a two-layer
 * representation:
 *   - Storage: ::tpy::Union<Dog, Cat>        (owns values)
 *   - Reference: std::variant<Dog*, Cat*>    (borrows values)
 *
 * to_ptr_variant() converts a storage variant reference into a pointer variant.
 * std::monostate members (representing None in nullable unions) pass through.
 *
 * The storage form is a class DERIVED from std::variant, so every trait here
 * asks for a variant's base by deduction rather than matching the exact type.
 */

#pragma once

#include <variant>
#include <type_traits>

#include "union_type.hpp"

namespace tpy {

namespace detail {
// Deduce the std::variant a type IS or DERIVES FROM; void for anything else.
// A partial specialisation on `std::variant<Ts...>` misses a derived class,
// and every storage-form union (`::tpy::Union<Ts...>`) is one.
template<typename... Ts> std::variant<Ts...> variant_base_of(const std::variant<Ts...>&);
void variant_base_of(...);
}  // namespace detail

template<typename T>
using variant_base_t =
    decltype(detail::variant_base_of(std::declval<const std::remove_cvref_t<T>&>()));

// Trait: is T a std::variant (or derived from one)? Used by the per-element
// tuple borrow/storage converters to dispatch a union element through the
// variant boundary helpers.
template<typename T>
inline constexpr bool is_variant_v = !std::is_void_v<variant_base_t<T>>;

// Trait: is T a pointer variant (every alternative is a pointer or monostate)?
// Distinguishes the borrow form std::variant<A*, B*> from the storage form
// ::tpy::Union<A, B>.
namespace detail {
template<typename V> struct ptr_variant_impl : std::false_type {};
template<typename... Ts> struct ptr_variant_impl<std::variant<Ts...>>
    : std::bool_constant<((std::is_pointer_v<Ts>
                           || std::is_same_v<Ts, std::monostate>) && ...)> {};
}  // namespace detail
template<typename T>
inline constexpr bool is_ptr_variant_v = detail::ptr_variant_impl<variant_base_t<T>>::value;

// Trait: does this pointer variant have const pointees (std::variant<const A*,
// ...>)? Selects to_const_ptr_variant vs to_ptr_variant. Every alternative must
// be a const pointer or monostate (a borrow-form union tuple element is
// uniform; requiring all alternatives keeps a hypothetical mixed-const variant
// from silently const-upgrading the mutable ones).
template<typename T> struct ptr_variant_const_pointee_impl : std::false_type {};
template<typename... Ts> struct ptr_variant_const_pointee_impl<std::variant<Ts...>>
    : std::bool_constant<((std::is_same_v<Ts, std::monostate>
                           || (std::is_pointer_v<Ts>
                               && std::is_const_v<std::remove_pointer_t<Ts>>)) && ...)> {};
template<typename T>
using ptr_variant_const_pointee = ptr_variant_const_pointee_impl<variant_base_t<T>>;

namespace detail {

// Map T -> T*, but monostate -> monostate
template<typename T>
struct ptr_of { using type = T*; };

template<>
struct ptr_of<std::monostate> { using type = std::monostate; };

// Map T -> const T*, but monostate -> monostate
template<typename T>
struct const_ptr_of { using type = const T*; };

template<>
struct const_ptr_of<std::monostate> { using type = std::monostate; };

// Try to convert alternative I: if active, emplace pointer into result.
// Returns true (short-circuits fold) on match.
template<std::size_t I, typename Result, typename Variant>
bool try_emplace_ptr(Result& r, Variant& v) {
    if (v.index() != I) return false;
    // The base, not the type itself. Both callers below already deduce the
    // base at their own parameter, so `Variant` is never a derived type today;
    // `variant_base_t` keeps the line correct for either.
    using Alt = std::variant_alternative_t<I, variant_base_t<Variant>>;
    if constexpr (std::is_same_v<Alt, std::monostate>) {
        r.template emplace<I>(std::monostate{});
    } else {
        r.template emplace<I>(&std::get<I>(v));
    }
    return true;
}

template<typename Result, typename Variant, std::size_t... Is>
Result to_ptr_variant_impl(Variant& v, std::index_sequence<Is...>) {
    Result r;
    (try_emplace_ptr<Is>(r, v) || ...);
    return r;
}

} // namespace detail

// Convert std::variant<Ts...>& -> std::variant<Ts*...> (monostate passthrough)
template<typename... Ts>
std::variant<typename detail::ptr_of<Ts>::type...>
to_ptr_variant(std::variant<Ts...>& v) {
    using Result = std::variant<typename detail::ptr_of<Ts>::type...>;
    return detail::to_ptr_variant_impl<Result>(v, std::index_sequence_for<Ts...>{});
}

// Convert const std::variant<Ts...>& -> std::variant<const Ts*...> (monostate passthrough)
template<typename... Ts>
std::variant<typename detail::const_ptr_of<Ts>::type...>
to_const_ptr_variant(const std::variant<Ts...>& v) {
    using Result = std::variant<typename detail::const_ptr_of<Ts>::type...>;
    return detail::to_ptr_variant_impl<Result>(v, std::index_sequence_for<Ts...>{});
}

// Convert mutable pointer variant to const pointer variant:
// std::variant<T*...> -> std::variant<const T*...>
// Handles monostate (nullable unions) by passing it through.
namespace detail {
template<typename Result>
struct to_const_visitor {
    template<typename T>
    Result operator()(T* p) const { return static_cast<const T*>(p); }
    Result operator()(std::monostate m) const { return m; }
};
} // namespace detail

template<typename Result, typename... Alts>
Result ptr_variant_to_const(const std::variant<Alts...>& v) {
    return std::visit(detail::to_const_visitor<Result>{}, v);
}

// Reverse: convert pointer variant to value variant (copies active member).
// Used when storing into fields/containers that own their values.
// ValueVariant = target type (e.g. std::variant<Dog, Cat>)
// PtrVariant = source type (e.g. std::variant<Dog*, Cat*>)
namespace detail {

// Visitor that converts each pointer alternative to its value type.
// Constructs the Result variant directly, avoiding default-construction
// (which would require the first alternative to be default-constructible).
template<typename Result>
struct deref_visitor {
    template<typename T>
    Result operator()(T* p) const { return Result{*p}; }

    Result operator()(std::monostate) const { return Result{std::monostate{}}; }
};

} // namespace detail

// Convert pointer variant to value variant: to_value_variant<std::variant<Dog,Cat>>(pv)
// Caller must specify the target value-variant type explicitly.
template<typename ValueVariant, typename... PtrAlts>
ValueVariant to_value_variant(const std::variant<PtrAlts...>& v) {
    return std::visit(detail::deref_visitor<ValueVariant>{}, v);
}

namespace detail {

// The trait answers generated code relies on, pinned in-header (compiled by
// every generated TU) because the storage form is a DERIVED variant: a trait
// that stopped deducing through the base would answer `false` silently and
// send a storage union down the wrong branch of borrow_value_elem.
struct variant_ref_pins {
    struct A { int x; };
    struct B { int y; };
    using SU = Union<A, B>;
    using PV = std::variant<A*, B*>;
    static_assert(std::is_same_v<variant_base_t<SU>, std::variant<A, B>>);
    static_assert(is_variant_v<SU>);
    static_assert(is_variant_v<std::variant<A, B>>);
    static_assert(!is_variant_v<int>);
    // A storage union is not a borrow form, whatever its head spells.
    static_assert(!is_ptr_variant_v<SU>);
    static_assert(is_ptr_variant_v<PV>);
    static_assert(!ptr_variant_const_pointee<PV>::value);
    static_assert(ptr_variant_const_pointee<std::variant<const A*, const B*>>::value);
};

}  // namespace detail

} // namespace tpy
