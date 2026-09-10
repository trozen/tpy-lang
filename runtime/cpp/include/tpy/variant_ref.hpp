/**
 * Converters between a union's two forms.
 *
 * Non-value unions (e.g. Dog | Cat where members are records) use a two-layer
 * representation, both spelled with the one type (union_type.hpp):
 *   - Storage: ::tpy::Union<Dog, Cat>    (owns the values)
 *   - Borrow:  ::tpy::Union<Dog*, Cat*>  (borrows them)
 *
 * to_ptr_variant() converts a storage variant reference into a borrow union.
 * std::monostate members (representing None in nullable unions) pass through.
 *
 * `Union` is DERIVED from std::variant, so every trait here asks for a base
 * by deduction rather than matching an exact type; the borrow question is the
 * ALTERNATIVE PACK, which is what the two forms actually differ in.
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

// Trait: is T the BORROW form of a union? One type spells both forms, so
// the question is the ALTERNATIVE PACK -- every alternative a pointer, plus a
// monostate for a nullable union's None (`detail::vc_borrow_pack`). Asked
// over the deduced base, so a `::tpy::Union` derived class answers for its
// pack rather than for its head.
namespace detail {
template<typename... Ts>
std::bool_constant<vc_borrow_pack<Ts...>> borrow_pack_of(const std::variant<Ts...>&);
std::false_type borrow_pack_of(...);
}  // namespace detail

template<typename T>
inline constexpr bool is_ptr_variant_v = decltype(
    detail::borrow_pack_of(std::declval<const std::remove_cvref_t<T>&>()))::value;

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

// Convert std::variant<Ts...>& -> Union<Ts*...> (monostate passthrough)
//
// Each converter below returns a borrow OF ITS ARGUMENT: the result holds
// `&std::get<I>(v)`. A temporary argument therefore hands back a dangling
// union, which ASan reports as a stack-use-after-scope at the first read,
// so the rvalue overload is deleted rather than left to bind.
template<typename... Ts>
Union<typename detail::ptr_of<Ts>::type...>
to_ptr_variant(std::variant<Ts...>& v) {
    using Result = Union<typename detail::ptr_of<Ts>::type...>;
    return detail::to_ptr_variant_impl<Result>(v, std::index_sequence_for<Ts...>{});
}

template<typename... Ts>
Union<typename detail::ptr_of<Ts>::type...>
to_ptr_variant(std::variant<Ts...>&&) = delete;

// Convert const std::variant<Ts...>& -> Union<const Ts*...> (monostate passthrough)
template<typename... Ts>
Union<typename detail::const_ptr_of<Ts>::type...>
to_const_ptr_variant(const std::variant<Ts...>& v) {
    using Result = Union<typename detail::const_ptr_of<Ts>::type...>;
    return detail::to_ptr_variant_impl<Result>(v, std::index_sequence_for<Ts...>{});
}

template<typename... Ts>
Union<typename detail::const_ptr_of<Ts>::type...>
to_const_ptr_variant(std::variant<Ts...>&&) = delete;

// A mutable borrow union reaches its read-borrow sibling through the type's
// own `Union::as_const()`, which needs no target spelling from the caller.

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
// every generated TU) because a union is a DERIVED variant: a trait that
// stopped deducing through the base would answer `false` silently and send a
// storage union down the wrong branch of borrow_value_elem.
struct variant_ref_pins {
    struct A { int x; };
    struct B { int y; };
    using SU = Union<A, B>;
    using BU = Union<A*, B*>;
    static_assert(std::is_same_v<variant_base_t<SU>, std::variant<A, B>>);
    static_assert(std::is_same_v<variant_base_t<BU>, std::variant<A*, B*>>);
    static_assert(is_variant_v<SU>);
    static_assert(is_variant_v<BU>);
    static_assert(is_variant_v<std::variant<A, B>>);
    static_assert(!is_variant_v<int>);
    // The two forms are one type over different packs, and the pack is
    // what the trait reads -- including through the base, so the bare
    // variant a `@native` signature may spell answers for its pack too.
    static_assert(!is_ptr_variant_v<SU>);
    static_assert(is_ptr_variant_v<BU>);
    static_assert(is_ptr_variant_v<std::variant<A*, B*>>);
    static_assert(!is_ptr_variant_v<std::variant<A, B>>);
    static_assert(!ptr_variant_const_pointee<BU>::value);
    static_assert(ptr_variant_const_pointee<Union<const A*, const B*>>::value);
    // The converters answer with the borrow form, so a lifted slot needs no
    // spelling of its own.
    static_assert(std::is_same_v<
        decltype(to_ptr_variant(std::declval<std::variant<A, B>&>())), BU>);
    static_assert(std::is_same_v<
        decltype(to_const_ptr_variant(std::declval<const std::variant<A, B>&>())),
        BU::const_form>);
};

}  // namespace detail

} // namespace tpy
