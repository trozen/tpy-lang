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
#include <type_traits>

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

// Tuple overload: the same rule per member. A member the tuple OWNS (a
// value, a `val_or_ref` over a value payload, an rvalue-reference member an
// owning combinator lends out of the container it holds) is moved out; a
// member it LENDS -- an lvalue-reference member (`std::tuple<int32_t, Cell&>`,
// what a combinator over a caller's container steps) or a `val_or_ref` over
// a reference payload -- stays the reference, so a collect into storage
// COPIES the source element rather than moving it out from under the
// container; a nested tuple recurses. `std::get` on a reference member is an
// lvalue exactly like an owned member's, so the member TYPE decides, and the
// result spells its members rather than deducing them (CTAD would decay
// every reference into a move).
template<typename M, typename V>
decltype(auto) unwrap_tuple_member(V& v) {
    if constexpr (std::is_lvalue_reference_v<M>) return (v);
    else return unwrap_ref_move(v);
}

template<typename R>
using tuple_member_out_t = std::conditional_t<
    std::is_lvalue_reference_v<R>, R, std::remove_cvref_t<R>>;

template<typename... Ts, std::size_t... Is>
auto unwrap_tuple_refs(std::tuple<Ts...>& t, std::index_sequence<Is...>) {
    return std::tuple<tuple_member_out_t<decltype(unwrap_tuple_member<Ts>(std::get<Is>(t)))>...>{
        unwrap_tuple_member<Ts>(std::get<Is>(t))...};
}

template<typename... Ts>
auto unwrap_ref_move(std::tuple<Ts...>& t) {
    return unwrap_tuple_refs(t, std::index_sequence_for<Ts...>{});
}

namespace detail {
// Whether an iterator step holds its value rather than lending an element its
// source keeps: a `val_or_ref` over a value payload, or a plain value.
template<typename V>
constexpr bool step_owns_value() {
    if constexpr (requires { typename V::is_val_or_ref_tag; }) return V::is_val;
    else return true;
}

// The type an owning binding holds for a step value of type `T`: `T` itself,
// except that a tuple's reference members become values of their own, nested
// tuples included -- a `std::tuple<int32_t, Cell&>` step lends its `Cell`.
template<typename T>
struct owned_value { using type = T; };
template<typename... Ms>
struct owned_value<std::tuple<Ms...>> {
    using type = std::tuple<typename owned_value<std::remove_cvref_t<Ms>>::type...>;
};
template<typename T>
using owned_value_t = typename owned_value<std::remove_cvref_t<T>>::type;
}

// The value an OWNING binding (an `Own[T]` loop variable, `auto&& x = ...`)
// takes from one step: a step that owns its value hands it over as `T&&`, so
// the binding holds it in place with no copy and no move; a step that lends an
// element its source keeps hands over a copy, so neither a move out of the
// binding nor a write through it reaches the source. A tuple comes back as a
// value whose every member is its own (`detail::owned_value_t`): a reference
// member, even of a tuple the step owns, is an element the source keeps, so it
// is copied -- the binding never aliases the source through a member.
template<typename S>
decltype(auto) own_bind(S& step) {
    if constexpr (!detail::step_owns_value<S>()) {
        return detail::owned_value_t<decltype(step.get())>(step.get());
    } else if constexpr (std::is_same_v<
            detail::owned_value_t<decltype(unwrap_ref_move(step))>,
            std::remove_cvref_t<decltype(unwrap_ref_move(step))>>) {
        return unwrap_ref_move(step);
    } else {
        return detail::owned_value_t<decltype(unwrap_ref_move(step))>(
            unwrap_ref_move(step));
    }
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
    // The step is this iterator's own: what it owns is bound in place, what
    // it lends is copied (see take_elem).
    decltype(auto) __tpy_take() { return own_bind(*current); }
    bool operator!=(NextSentinel) const { return current.has_value(); }
};

// The begin/end twin of own_bind: the element a begin/end loop hands an
// OWNING binding (an `Own[T]` loop variable) -- an rvalue reference to a step
// the iterator itself owns (`__tpy_take()`) or to a move iterator's element;
// any other element is a container's own, lent to the loop, so the binding
// gets a copy (a prvalue) and the container keeps it.
template<typename It>
decltype(auto) take_elem(It& it) {
    if constexpr (requires { it.__tpy_take(); }) return it.__tpy_take();
    else if constexpr (std::is_rvalue_reference_v<decltype(*it)>) return *it;
    else return detail::owned_value_t<decltype(*it)>(*it);
}

// CRTP mixin: adds begin()/end() to any class with __next__().
template<typename Derived, typename T>
struct next_iter_mixin {
    NextIterator<Derived, T> begin() {
        return NextIterator<Derived, T>(static_cast<Derived*>(this));
    }
    NextSentinel end() { return {}; }
};

// Declared here so the consumers in earlier headers can name them; defined in
// dunder.hpp, after every free `__iter__` overload their qualified lookup must
// see. `iter_range(x)` is what a range-for over any iterable loops over: `x`
// itself when it has begin()/end(), else the iterator its `__iter__()` returns.
// `iter_of(x)` is that iterator alone, for a loop that steps `__next__()`.
template<typename X> decltype(auto) iter_range(X& x);
template<typename X> decltype(auto) iter_of(X& x);

} // namespace tpy
