/**
 * TurboPython Runtime - OwnIter and the hand-over of an owning element slot
 *
 * `own_iter(c)` is the C++ implementation of a type's consuming
 * `__iter__(self: Own[Self])`: it gives the object up to an iterator that
 * owns it and yields its elements by move. This header holds the
 * `std::vector` case (moving it in is a pointer swap), the `std::array` one
 * (moved in element by element) and the one for a record, whose consuming
 * `__iter__` is its `&&`-qualified member; `ordered_set.hpp` and
 * `ordered_map.hpp` add `set` and `dict` through `own_iter_of`. Unconsumed
 * elements are destructed when the iterator is dropped.
 *
 * Each iterator implements both C++ range (begin/end with move iterators) and
 * the TurboPython Iterator protocol (__next__/__iter__).
 *
 * A parameter typed `Iterable[Own[T]]` (or any iteration protocol whose step
 * is `Own[T]`) deduces its C++ type from the argument: a container the caller
 * hands over arrives as a value (`T_xs = std::vector<R>`), one it lends as an
 * lvalue reference (`T_xs = std::vector<R>&`). `handover_range<A>(c)` (here) /
 * `handover_iter<A>(c)` (dunder.hpp) traverse such a parameter: an object
 * handed over is moved into its own consuming iterator, anything else is
 * iterated as it lends, its elements copied by the owning binding.
 */

#pragma once

#include "core.hpp"
#include "next_iter.hpp"
#include "type_traits.hpp"

#include <array>
#include <concepts>
#include <cstddef>
#include <expected>
#include <iterator>
#include <memory>
#include <ranges>
#include <type_traits>
#include <utility>
#include <vector>

namespace tpy {

template<typename T, typename C = std::vector<T>>
struct OwnIter {
    C data;
    std::size_t pos = 0;

    explicit OwnIter(C&& c) : data(std::move(c)) {}

    // NativeIterable: range-based iteration with move semantics.
    // Do not mix range-based (begin/end) and __next__-based iteration on the
    // same instance -- both advance shared state and the result is undefined.
    auto begin() { return std::make_move_iterator(data.begin() + static_cast<std::ptrdiff_t>(pos)); }
    auto end()   { return std::make_move_iterator(data.end()); }

    // The elements not yet taken.
    std::size_t size() const { return data.size() - pos; }

    // Iterator protocol
    std::expected<T, StopIteration> __next__() {
        if (pos >= data.size()) return tpy::make_unexpected(StopIteration{});
        return std::move(data[pos++]);
    }

    OwnIter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const OwnIter&) {
        return os << "<own_iter>";
    }
};

// The consuming iterator of the container type `C`, specialized by the header
// that defines the container (here `std::vector` and `std::array`;
// `ordered_set.hpp`, `ordered_map.hpp`). A partial specialization matches the
// exact type only: a class derived from a container -- a user `list`
// subclass, `::tpy::Bytes` -- has an iteration of its own (an overriding
// `__iter__`, a different element form) that a deduction through the base
// would bypass. Any type that names a container has included the header
// that specializes it, whatever the include order.
template<typename C> struct own_iter_of {};

template<typename T> struct own_iter_of<std::vector<T>> { using type = OwnIter<T>; };

template<typename T, std::size_t N> struct own_iter_of<std::array<T, N>> {
    using type = OwnIter<T, std::array<T, N>>;
};

// `C` deduced by forwarding: an lvalue deduces a reference, which no
// specialization matches. Every stub `__iter__(self: Own[Self])` has exactly
// one `own_iter` overload here (or beside its container), and sema's
// `consuming_iter_of` must agree: a mismatch moves an argument the callee
// then lends and copies.
template<typename C>
    requires requires { typename own_iter_of<C>::type; }
typename own_iter_of<C>::type own_iter(C&& c) {
    return typename own_iter_of<C>::type{std::move(c)};
}

// Whether `It` is one of the runtime's own consuming iterators, whose
// `size()` counts what it has left to yield: specialized beside each
// (`OwnIter` here, `OwnIterSet` / `OwnIterDict` in their headers). A trait in
// `tpy::` rather than a member a user class could declare.
template<typename It> struct sized_consumer : std::false_type {};

template<typename T, typename C>
struct sized_consumer<OwnIter<T, C>> : std::true_type {};

// Reserve `out` for what a consuming iterator has left to yield, when it
// is one of the runtime's own (`sized_consumer`). Any other iterator -- a
// generator frame, a user iterator class, whatever its methods are named or
// return -- is not asked: CPython runs no user code to presize a
// comprehension.
template<typename Out, typename It>
void reserve_if_sized(Out& out, const It& it) {
    if constexpr (sized_consumer<It>::value) {
        out.reserve(it.size());
    }
}

namespace detail {
// `T` itself declares a consuming `__iter__`: a member callable only on an
// rvalue, `R (T::*)() &&`. Declaration evidence, not a comparison of the
// lvalue and rvalue calls' results: a lending `__iter__` an rvalue merely
// binds to (unqualified or `const&`) does not hand the object over. One a
// base declares does not count -- sema gives a subclass no consuming
// `__iter__` of its own, and the base's `&&` member would take `*this` as
// the base (BUGS.md#consuming-iter-not-inherited).
template<typename T>
concept declares_consuming_iter =
    std::is_class_v<T> && !std::is_const_v<T>
    && requires { []<typename R>(R (T::*)() &&) {}(&T::__iter__); };
} // namespace detail

// A record's consuming `__iter__` (`def __iter__(self: Own[Self])`) is its
// `&&` member; `T` deduced by forwarding, so an lvalue (the caller kept the
// object) never matches.
template<typename T>
    requires (!std::is_reference_v<T>) && detail::declares_consuming_iter<T>
decltype(auto) own_iter(T&& x) {
    return std::move(x).__iter__();
}

// The object `c` (of type `S`) under parameter type `A` is handed over:
// `A` is not an lvalue reference (the caller gave the object up) and its type
// has a consuming iteration (`own_iter` takes it). Anything else passed by
// value -- a generator, a `copy_iter` / `own_iter` adapter, a view, a
// container subclass without a consuming `__iter__` -- iterates as itself.
template<typename A, typename S>
constexpr bool hands_over_v =
    !std::is_lvalue_reference_v<A>
    && requires { ::tpy::own_iter(std::declval<std::remove_reference_t<S>&&>()); };

template<typename It>
using iter_next_t = decltype(std::declval<It&>().__next__());

namespace detail {
// What a range-for takes as is: a begin()/end() pair that is a real iterator
// and sentinel (a next_iter_mixin type, whose sentinel differs from its
// iterator, included) or a standard range. A class that merely has methods
// named begin/end is not one.
template<typename X>
concept range_for_iterable =
    (requires(X& x) {
        *x.begin();
        x.begin() != x.end();
        ++std::declval<decltype(x.begin())&>();
    }
    || std::ranges::range<X&>)
    && !::tpy::iterates_by_dunder<X>;

// The begin()/end() face of the iterator an iterable's __iter__() returned,
// held in the form it came in: any reference aliases the iterator (a const
// one steps only through a const `__next__`, else the build fails -- a copy
// would step a stale duplicate), a value is built in place. Never moved:
// NextIterator points at `it`. Its `__next__` (and no `__iter__`) lets a
// consumer that steps by protocol (`iter_of`) take it as its own iterator.
template<typename It>
struct iter_protocol_range {
    It it;
    using iter_t = std::remove_reference_t<It>;
    using elem_t = typename iter_next_t<iter_t>::value_type;
    NextIterator<iter_t, elem_t> begin() {
        return NextIterator<iter_t, elem_t>(std::addressof(it));
    }
    NextSentinel end() { return {}; }
    decltype(auto) __next__() { return it.__next__(); }
};

// The begin()/end() face of an iterator a traversal already holds -- `It` a
// reference when it is lent, a value when the face owns it: the iterator
// itself when it is range-for iterable (a container's consuming iterator
// moves each element straight out of its storage), else its `__next__`
// stepped in place -- never its `__iter__()` again, which CPython does not
// call on the iterator.
template<typename It>
using step_face_t = std::conditional_t<range_for_iterable<std::remove_reference_t<It>>,
                                       It, iter_protocol_range<It>>;
} // namespace detail

template<typename It>
decltype(auto) step_range(It& it) {
    using Face = detail::step_face_t<It&>;
    if constexpr (std::is_reference_v<Face>) return (it);
    else return Face{it};
}

// The begin()/end() face of the parameter `c` under parameter type `A`: the
// step face of the consuming iterator it was handed over to (owned, as the
// iterator is a temporary), else `c` itself.
template<typename A, typename C>
decltype(auto) handover_range(C& c) {
    if constexpr (hands_over_v<A, C>) {
        using It = decltype(::tpy::own_iter(std::move(c)));
        return detail::step_face_t<It>{::tpy::own_iter(std::move(c))};
    } else {
        return (c);
    }
}

} // namespace tpy
