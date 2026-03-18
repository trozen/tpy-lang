/**
 * Pointer-variant utilities for non-value union types.
 *
 * Non-value unions (e.g. Dog | Cat where members are records) use a two-layer
 * representation:
 *   - Storage: std::variant<Dog, Cat>        (owns values)
 *   - Reference: std::variant<Dog*, Cat*>    (borrows values)
 *
 * to_ptr_variant() converts a storage variant reference into a pointer variant.
 * std::monostate members (representing None in nullable unions) pass through.
 */

#pragma once

#include <variant>
#include <type_traits>

namespace tpy {

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
    using Alt = std::variant_alternative_t<I, std::remove_cvref_t<Variant>>;
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

template<std::size_t I, typename Result, typename PtrVariant>
bool try_deref_emplace(Result& r, const PtrVariant& v) {
    if (v.index() != I) return false;
    using Alt = std::variant_alternative_t<I, PtrVariant>;
    if constexpr (std::is_same_v<Alt, std::monostate>) {
        r.template emplace<I>(std::monostate{});
    } else {
        r.template emplace<I>(*std::get<I>(v));
    }
    return true;
}

template<typename Result, typename PtrVariant, std::size_t... Is>
Result to_value_variant_impl(const PtrVariant& v, std::index_sequence<Is...>) {
    Result r;
    (try_deref_emplace<Is>(r, v) || ...);
    return r;
}

} // namespace detail

// Convert pointer variant to value variant: to_value_variant<std::variant<Dog,Cat>>(pv)
// Caller must specify the target value-variant type explicitly.
template<typename ValueVariant, typename... PtrAlts>
ValueVariant to_value_variant(const std::variant<PtrAlts...>& v) {
    return detail::to_value_variant_impl<ValueVariant>(v, std::index_sequence_for<PtrAlts...>{});
}

} // namespace tpy
