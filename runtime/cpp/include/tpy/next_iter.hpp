/**
 * TurboPython Runtime - next_iter adapter
 *
 * Provides begin()/end() for any type with __next__() returning
 * std::expected<T, StopIteration>. This bridges the TPy Iterator protocol
 * (__next__/__iter__) to C++ range-for loops.
 *
 * Usage: inherit from next_iter_mixin<Derived, T> to gain begin()/end().
 */

#pragma once

#include "core.hpp"

#include <expected>
#include <tuple>
#include <optional>

namespace tpy {

// Unwrap val_or_ref<T> via .get(), pass through plain T unchanged.
template<typename T>
decltype(auto) unwrap_ref(T& v) {
    if constexpr (requires { typename T::is_val_or_ref_tag; }) {
        return v.get();
    } else {
        return (v);
    }
}

// Move-aware unwrap: moves plain values out (for expression-level unwrap
// where the source is temporary), returns reference for val_or_ref.
template<typename T>
decltype(auto) unwrap_ref_move(T& v) {
    if constexpr (requires { typename T::is_val_or_ref_tag; }) {
        return v.get();
    } else {
        return std::move(v);
    }
}

// Tuple overload: unwrap val_or_ref from each element.
// Produces tuple<string, Point> from tuple<string, val_or_ref<Point>>.
template<typename Tuple, std::size_t... Is>
auto unwrap_tuple_refs(Tuple& t, std::index_sequence<Is...>) {
    return std::tuple{unwrap_ref_move(std::get<Is>(t))...};
}

template<typename... Ts>
auto unwrap_ref_move(std::tuple<Ts...>& t) {
    return unwrap_tuple_refs(t, std::index_sequence_for<Ts...>{});
}

struct NextSentinel {};

template<typename Parent, typename T>
struct NextIterator {
    Parent* parent;
    std::optional<T> current;

    NextIterator& operator++() {
        auto r = parent->__next__();
        // Destroy-then-construct, never assign: T may carry references
        // (zip/enumerate yield std::tuple<..., U&>), and assigning into an
        // engaged optional would write the NEW element THROUGH the old
        // element's reference, corrupting the source container.
        current.reset();
        if (r.has_value()) current.emplace(std::move(*r));
        return *this;
    }
    decltype(auto) operator*() { return unwrap_ref(*current); }
    decltype(auto) operator*() const { return unwrap_ref(*current); }
    bool operator!=(NextSentinel) const { return current.has_value(); }
};

// CRTP mixin: adds begin()/end() to any class with __next__().
template<typename Derived, typename T>
struct next_iter_mixin {
    NextIterator<Derived, T> begin() {
        NextIterator<Derived, T> it{static_cast<Derived*>(this), std::nullopt};
        ++it;  // prime with first element
        return it;
    }
    NextSentinel end() { return {}; }
};

} // namespace tpy
