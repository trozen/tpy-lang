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
#include <memory>
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
//
// A val_or_ref over a VALUE payload owns its copy -- the step result is
// consumed here and the wrapper dies with it -- so move that copy out: the
// bare slot spelling hands back a `T&&`, and a slot that only lent an lvalue
// would make every collect/list()/extend copy where the monomorphic twin
// moved. A REFERENCE payload points at storage someone else owns, so it
// stays an lvalue.
template<typename T>
decltype(auto) unwrap_ref_move(T& v) {
    if constexpr (requires { typename T::is_val_or_ref_tag; }) {
        if constexpr (T::is_val) return std::move(v.get());
        else return v.get();
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
    using step_t = decltype(std::declval<Parent&>().__next__());
    // Only what `__next__` yields is stored, so the mixin's declared element
    // type is checked against it here or nowhere.
    static_assert(std::is_same_v<typename step_t::value_type, T>);
    Parent* parent;
    // The step result itself, not a copy of its value: one engaged flag to
    // test per element instead of two, and no move out of the result.
    step_t current;

    explicit NextIterator(Parent* p) : parent(p), current(p->__next__()) {}

    NextIterator& operator++() {
        // Destroy-then-construct, never assign: T may carry references
        // (zip/enumerate yield std::tuple<..., U&>), and assigning into an
        // engaged slot would write the NEW element THROUGH the old
        // element's reference, corrupting the source container.
        if constexpr (std::is_nothrow_move_constructible_v<step_t>) {
            // Step first, so a throwing __next__ leaves `current` alive.
            step_t next = parent->__next__();
            std::destroy_at(&current);
            std::construct_at(&current, std::move(next));
        } else {
            // A result that may throw on move cannot be stepped first, so this
            // arm runs `__next__` with no live result; nothing a producer owns
            // lives in `current` (a borrowed element is a trivially movable
            // pointer slot and takes the arm above).
            std::destroy_at(&current);
            try {
                ::new (static_cast<void*>(&current)) step_t(parent->__next__());
            } catch (...) {
                // `current` must hold a live object when the iterator dies.
                ::new (static_cast<void*>(&current))
                    step_t(std::unexpect, typename step_t::error_type{});
                throw;
            }
        }
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
        return NextIterator<Derived, T>(static_cast<Derived*>(this));
    }
    NextSentinel end() { return {}; }
};

} // namespace tpy
