/**
 * TurboPython Runtime - Iterator Builtins
 *
 * enumerate(), zip(), reversed(), map(), and filter() implementations.
 * Depends on: next_iter.hpp, dunder.hpp (for __iter__, __len__, __getitem__)
 */

#pragma once

#include "next_iter.hpp"
#include "dunder.hpp"
#include "type_traits.hpp"

#include <concepts>
#include <cstdint>
#include <expected>
#include <iterator>
#include <memory>
#include <new>
#include <optional>
#include <ranges>
#include <tuple>
#include <utility>

namespace tpy {

namespace detail {

// A compiled record whose own __iter__() hands back a separate iterator
// object. That call is user code, so it is observable and cannot wait for the
// first pull, and the iterator it returns may point into the record, so the
// record cannot move afterwards. Owned, it is PINNED: the cursor is made at
// the combinator call and the holder does not move. Where tpyc can, it binds
// such a temporary to a local and passes the lvalue instead, which selects a
// borrowing flavor and keeps the combinator movable. A runtime or @native type
// with the same shape (a dict view, a set) is owned like a container: its
// __iter__ runs at the first pull.
template<typename C>
concept separate_user_iterator =
    is_tpy_record_v<C> && requires(C& c) { c.__iter__(); }
    && !is_self_iterator_v<C>;

// Storage a value is built INTO, straight from the prvalue that makes it, so a
// move-only or immovable T works. `std::optional::emplace` cannot do that (it
// constructs from its arguments, and an argument wrapper with a conversion
// operator loses overload resolution to any converting ctor template on T).
template<typename T>
class elided_slot {
    union { T value_; };
    bool has_ = false;
public:
    elided_slot() {}
    ~elided_slot() { if (has_) value_.~T(); }

    elided_slot(elided_slot&& o) noexcept(std::is_nothrow_move_constructible_v<T>)
        requires std::move_constructible<T> {
        if (o.has_) {
            ::new (static_cast<void*>(std::addressof(value_))) T(std::move(o.value_));
            has_ = true;
        }
    }
    elided_slot& operator=(elided_slot&&) = delete;

    bool has() const { return has_; }
    // `make()` returns T, by value or as a reference to copy from.
    template<typename F>
    void fill(F&& make) {
        ::new (static_cast<void*>(std::addressof(value_))) T(make());
        has_ = true;
    }
    T* operator->() { return std::addressof(value_); }
};

// The iterator a non-self source's __iter__() handed back, held in the form it
// came in. A non-const reference is ALIASED: that iterator lives in the source
// or beyond it (a member the record delegates to, `*this` typed as a base),
// and CPython advances that very object. A value is built in place. A const
// reference is copied -- no pull can go through it.
template<typename C>
class iter_cursor {
    using R = decltype(tpy::__iter__(std::declval<C&>()));
    static constexpr bool aliased =
        std::is_lvalue_reference_v<R> && !std::is_const_v<std::remove_reference_t<R>>;
    using held_t = std::conditional_t<aliased, std::remove_reference_t<R>*,
                                      elided_slot<std::decay_t<R>>>;
    held_t held_{};
public:
    bool seeded() const {
        if constexpr (aliased) return held_ != nullptr;
        else return held_.has();
    }
    void seed(C& c) {
        if constexpr (aliased) held_ = std::addressof(tpy::__iter__(c));
        else held_.fill([&]() -> R { return tpy::__iter__(c); });
    }
    decltype(auto) next() { return held_->__next__(); }
};

// The source an owning combinator holds by value, pulled through __next__().
// No cursor exists while the combinator may still be moved: a self-iterator
// needs none, and a container's is made at the first pull -- building it has
// no side effect and nothing else can reach a container the combinator owns,
// so the delay is unobservable. Whether a STARTED self-iterator may still be
// relocated is that source's own contract, not this holder's.
template<typename C>
class owned_iter_source {
    struct no_cursor {};
    using cursor_t = std::conditional_t<is_self_iterator_v<C>, no_cursor, iter_cursor<C>>;
    C src_;
    [[no_unique_address]] cursor_t cur_;
public:
    explicit owned_iter_source(C&& c) : src_(std::move(c)) {}

    owned_iter_source(owned_iter_source&& o)
        noexcept(std::is_nothrow_move_constructible_v<C>)
        requires (!separate_user_iterator<C>)
        : src_(std::move(o.src_)) {
#ifndef NDEBUG
        if constexpr (!is_self_iterator_v<C>) {
            if (o.cur_.seeded()) tpy_panic("owning combinator moved after its first pull");
        }
#endif
    }
    owned_iter_source& operator=(owned_iter_source&&) = delete;

    // The observable half of iter(): a self-iterator's __iter__ is user code
    // that CPython runs at the combinator call. The combinator calls start()
    // on its sources in argument order.
    void start() {
        if constexpr (is_self_iterator_v<C>) (void)tpy::__iter__(src_);
        else if constexpr (separate_user_iterator<C>) cur_.seed(src_);
    }

    decltype(auto) __next__() {
        if constexpr (is_self_iterator_v<C>) {
            return src_.__next__();
        } else {
            if (!cur_.seeded()) [[unlikely]] cur_.seed(src_);
            return cur_.next();
        }
    }
};

// The begin/end twin of owned_iter_source, for the direct flavors that hand
// out references to the container's own elements. A random-access container
// keeps a POSITION instead of iterators: nothing points into it, so it moves at
// any time and the pull loop stays free of a seeded-yet check. Any other
// container makes its iterators at the first pull.
template<typename C>
class owned_range_source {
    using It = begin_iter_t<C>;
    static constexpr bool indexed = std::random_access_iterator<It>;
    struct position { std::ptrdiff_t at = 0; };
    using cursor_t = std::conditional_t<indexed, position,
                                        std::optional<std::pair<It, It>>>;
    C src_;
    cursor_t cur_;
public:
    explicit owned_range_source(C&& c) : src_(std::move(c)) {}

    owned_range_source(owned_range_source&& o)
        noexcept(std::is_nothrow_move_constructible_v<C>)
        : src_(std::move(o.src_)) {
        if constexpr (indexed) {
            cur_ = o.cur_;
        } else {
#ifndef NDEBUG
            if (o.cur_.has_value()) tpy_panic("owning combinator moved after its first pull");
#endif
        }
    }
    owned_range_source& operator=(owned_range_source&&) = delete;

    bool done() {
        if constexpr (indexed) {
            return cur_.at == std::ranges::end(src_) - std::ranges::begin(src_);
        } else {
            if (!cur_) [[unlikely]] cur_.emplace(src_.begin(), src_.end());
            return cur_->first == cur_->second;
        }
    }

    // The next element, by reference into the owned container. Only after a
    // done() that answered false.
    decltype(auto) take() {
        if constexpr (indexed) return src_.begin()[cur_.at++];
        else return *cur_->first++;
    }
};

// An lvalue argument of a combinator that also owns a temporary one. A
// self-iterator is pulled through a pointer: copying it would advance a
// private copy where CPython advances the caller's object.
template<typename C>
class borrowed_iter_source {
    struct no_cursor {};
    using cursor_t = std::conditional_t<is_self_iterator_v<C>, no_cursor, iter_cursor<C>>;
    C* src_;
    [[no_unique_address]] cursor_t cur_;
public:
    explicit borrowed_iter_source(C& c) : src_(&c) {}

    // Runs __iter__ now, in the combinator's argument order: the iterator
    // points into the caller's object, so nothing here needs to wait.
    void start() {
        if constexpr (is_self_iterator_v<C>) (void)tpy::__iter__(*src_);
        else cur_.seed(*src_);
    }

    decltype(auto) __next__() {
        if constexpr (is_self_iterator_v<C>) return src_->__next__();
        else return cur_.next();
    }
};

// The member type an all-lvalue combinator holds an __iter__() result as: a
// non-const reference stays a reference (the iterator is advanced in place),
// anything else is a value -- a prvalue moved in, a const reference copied,
// since nothing can pull through it.
template<typename R>
using iter_member_t = std::conditional_t<
    std::is_lvalue_reference_v<R> && !std::is_const_v<std::remove_reference_t<R>>,
    R, std::decay_t<R>>;

// Per-argument holder of a mixed combinator: X is `C&` for an lvalue argument
// and `C` for a temporary.
template<typename X>
using arg_source_t = std::conditional_t<
    std::is_lvalue_reference_v<X>,
    borrowed_iter_source<std::remove_reference_t<X>>,
    owned_iter_source<X>>;

} // namespace detail

// -- enumerate --

template<typename T, typename Iter>
class enumerate_iter : public next_iter_mixin<enumerate_iter<T, Iter>, std::tuple<int32_t, T>> {
    Iter iter_;
    int32_t index_;  // TPy uses int32; Python enumerate uses arbitrary-precision
public:
    // forward, not move: a self-iterator arrives as `Self&` and is advanced
    // in place. A template because `Iter` may be a VALUE copied from a const
    // reference (detail::iter_member_t).
    template<typename U>
        requires std::constructible_from<Iter, U&&>
              && (!std::same_as<std::remove_cvref_t<U>, enumerate_iter>)
    enumerate_iter(U&& iter, int32_t start = 0)
        : iter_(std::forward<U>(iter)), index_(start) {}

    std::expected<std::tuple<int32_t, T>, StopIteration> __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, T>{index_++, unwrap_ref(*r)};
    }

    enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// Owning variant: moves the container in so rvalue arguments don't dangle.
template<typename T, typename Container>
class owning_enumerate_iter
    : public next_iter_mixin<owning_enumerate_iter<T, Container>, std::tuple<int32_t, T>> {
    detail::owned_iter_source<Container> src_;
    int32_t index_;
public:
    owning_enumerate_iter(Container&& c, int32_t start = 0)
        : src_(std::move(c)), index_(start) { src_.start(); }

    std::expected<std::tuple<int32_t, T>, StopIteration> __next__() {
        auto r = src_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, T>{index_++, unwrap_ref(*r)};
    }

    owning_enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// Direct-iteration variant: uses C++ begin/end to get references to container
// elements instead of going through __iter__/__next__() which copies.
template<typename T, typename Container>
class enumerate_direct_iter
    : public next_iter_mixin<enumerate_direct_iter<T, Container>,
                             std::tuple<int32_t, val_or_ref_t<T>>> {
    using CppIter = decltype(std::declval<Container&>().begin());
    Container& container_;
    CppIter it_;
    CppIter end_;
    int32_t index_;
public:
    enumerate_direct_iter(Container& c, int32_t start = 0)
        : container_(c), it_(c.begin()), end_(c.end()), index_(start) {}

    std::expected<std::tuple<int32_t, val_or_ref_t<T>>, StopIteration> __next__() {
        if (it_ == end_) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, val_or_ref_t<T>>{index_++, *it_++};
    }

    enumerate_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const enumerate_direct_iter&) {
        return os << "<enumerate>";
    }
};

// Owning direct-iteration variant for rvalue containers.
template<typename T, typename Container>
class owning_enumerate_direct_iter
    : public next_iter_mixin<owning_enumerate_direct_iter<T, Container>,
                             std::tuple<int32_t, val_or_ref_t<T>>> {
    detail::owned_range_source<Container> src_;
    int32_t index_;
public:
    owning_enumerate_direct_iter(Container&& c, int32_t start = 0)
        : src_(std::move(c)), index_(start) {}

    std::expected<std::tuple<int32_t, val_or_ref_t<T>>, StopIteration> __next__() {
        if (src_.done()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, val_or_ref_t<T>>{index_++, src_.take()};
    }

    owning_enumerate_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_enumerate_direct_iter&) {
        return os << "<enumerate>";
    }
};

namespace detail {
// Requires homogeneous begin/end (same iterator type) so enumerate_direct_iter
// can store both as CppIter. Excludes next_iter_mixin-derived types whose
// begin() returns NextIterator<...> but end() returns NextSentinel.
template<typename T>
concept has_begin_end = requires(T& t) { t.begin(); t.end(); }
    && std::same_as<decltype(std::declval<T&>().begin()), decltype(std::declval<T&>().end())>;

// Extract Nth parameter type from a callable (function pointer, lambda, std::function).
// Used by map_multi_iter to forward Own[T] args as rvalue refs.
// Generic lambdas (auto params) are not supported -- tpyc always emits
// monomorphic lambdas with concrete parameter types.
template<typename F> struct fn_params;
template<typename R, typename... A> struct fn_params<R(*)(A...)> { using types = std::tuple<A...>; };
template<typename R, typename... A> struct fn_params<R(&)(A...)> { using types = std::tuple<A...>; };
template<typename C, typename R, typename... A> struct fn_params<R(C::*)(A...) const> { using types = std::tuple<A...>; };
template<typename C, typename R, typename... A> struct fn_params<R(C::*)(A...)> { using types = std::tuple<A...>; };

template<typename F>
concept has_call_op = requires { &std::remove_cvref_t<F>::operator(); };

template<has_call_op F> struct fn_params<F> : fn_params<decltype(&std::remove_cvref_t<F>::operator())> {};

template<typename F, size_t N>
using fn_param_t = std::tuple_element_t<N, typename fn_params<std::remove_cvref_t<F>>::types>;
}

// lvalue: direct iteration for containers (preserves references),
// __iter__/__next__() fallback for TPy iterators
template<typename T, typename Iterable>
auto builtin_enumerate(Iterable& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return enumerate_direct_iter<T, Iterable>(iterable);
    } else {
        return enumerate_iter<T, detail::iter_member_t<decltype(tpy::__iter__(iterable))>>(tpy::__iter__(iterable));
    }
}

// rvalue: own the container to prevent dangling iterators
template<typename T, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_enumerate(Iterable&& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return owning_enumerate_direct_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable));
    } else {
        return owning_enumerate_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable));
    }
}

// lvalue with start
template<typename T, typename Iterable>
auto builtin_enumerate_start(Iterable& iterable, int32_t start) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return enumerate_direct_iter<T, Iterable>(iterable, start);
    } else {
        return enumerate_iter<T, detail::iter_member_t<decltype(tpy::__iter__(iterable))>>(tpy::__iter__(iterable), start);
    }
}

// rvalue with start
template<typename T, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_enumerate_start(Iterable&& iterable, int32_t start) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return owning_enumerate_direct_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable), start);
    } else {
        return owning_enumerate_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable), start);
    }
}

// -- zip (variadic) --

// Tag to carry element types through partial specialization (two-pack workaround).
template<typename... Ts> struct zip_types {};

namespace detail {

// Advance all iterators into optionals; returns false if any is exhausted.
// Uses unwrap() to unwrap val_or_ref from native_iterator __next__().
template<typename Tuple, typename... Opts, std::size_t... Is>
bool zip_advance(Tuple& iters, std::tuple<Opts...>& opts, std::index_sequence<Is...>) {
    bool ok = true;
    // && short-circuits: once ok is false, remaining lambdas are not called.
    // Matches Python zip() -- stop at shortest without over-advancing.
    ((ok = ok && [&]{
        auto r = std::get<Is>(iters).__next__();
        if (!r.has_value()) return false;
        std::get<Is>(opts).emplace(tpy::unwrap_ref(*r));
        return true;
    }()), ...);
    return ok;
}

// Build a tuple by moving out of the optionals.
template<typename... Ts, std::size_t... Is>
std::tuple<Ts...> zip_collect(std::tuple<std::optional<Ts>...>& opts, std::index_sequence<Is...>) {
    return std::tuple<Ts...>{std::move(*std::get<Is>(opts))...};
}

} // namespace detail

// Primary template (never instantiated directly).
template<typename TypeTag, typename... Iters>
class zip_iter;

// Partial specialization unpacks the element types from the tag.
template<typename... Ts, typename... Iters>
class zip_iter<zip_types<Ts...>, Iters...>
    : public next_iter_mixin<zip_iter<zip_types<Ts...>, Iters...>, std::tuple<Ts...>> {
    std::tuple<Iters...> iters_;
public:
    // forward, not move: a self-iterator argument arrives as `Self&` and is
    // advanced in place. A template because an `Iters` may be a VALUE copied
    // from a const reference (detail::iter_member_t).
    template<typename... Us>
        requires (sizeof...(Us) == sizeof...(Iters))
              && (std::constructible_from<Iters, Us&&> && ...)
    explicit zip_iter(Us&&... iters) : iters_(std::forward<Us>(iters)...) {}

    std::expected<std::tuple<Ts...>, StopIteration> __next__() {
        std::tuple<std::optional<Ts>...> opts;
        if (!detail::zip_advance(iters_, opts, std::index_sequence_for<Iters...>{}))
            return tpy::make_unexpected(StopIteration{});
        return detail::zip_collect<Ts...>(opts, std::index_sequence_for<Ts...>{});
    }

    zip_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const zip_iter&) {
        return os << "<zip>";
    }
};

template<typename TypeTag, typename... Containers>
class owning_zip_iter;

template<typename... Ts, typename... Containers>
class owning_zip_iter<zip_types<Ts...>, Containers...>
    : public next_iter_mixin<owning_zip_iter<zip_types<Ts...>, Containers...>, std::tuple<Ts...>> {
    std::tuple<detail::arg_source_t<Containers>...> srcs_;
public:
    // start() in the body, not in the holders' ctors: a tuple constructs its
    // members in an unspecified order, and __iter__ is observable.
    // Constrained so that a pinned zip does not claim to be movable through
    // this template.
    template<typename... Us>
        requires (sizeof...(Us) == sizeof...(Containers))
              && (std::constructible_from<detail::arg_source_t<Containers>, Us&&> && ...)
    explicit owning_zip_iter(Us&&... cs) : srcs_(std::forward<Us>(cs)...) {
        std::apply([](auto&... s) { (s.start(), ...); }, srcs_);
    }

    // Spelled: std::tuple reports itself movable whatever it holds.
    owning_zip_iter(owning_zip_iter&&)
        requires (std::move_constructible<detail::arg_source_t<Containers>> && ...)
        = default;

    std::expected<std::tuple<Ts...>, StopIteration> __next__() {
        std::tuple<std::optional<Ts>...> opts;
        if (!detail::zip_advance(srcs_, opts, std::index_sequence_for<Containers...>{}))
            return tpy::make_unexpected(StopIteration{});
        return detail::zip_collect<Ts...>(opts, std::index_sequence_for<Ts...>{});
    }

    owning_zip_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_zip_iter&) {
        return os << "<zip>";
    }
};

// Direct-iteration zip: uses C++ begin/end for reference-preserving iteration.
template<typename TypeTag, typename... Containers>
class zip_direct_iter;

template<typename... Ts, typename... Containers>
class zip_direct_iter<zip_types<Ts...>, Containers...>
    : public next_iter_mixin<zip_direct_iter<zip_types<Ts...>, Containers...>,
                             std::tuple<val_or_ref_t<Ts>...>> {
    std::tuple<decltype(std::declval<Containers&>().begin())...> its_;
    std::tuple<decltype(std::declval<Containers&>().end())...> ends_;

    template<std::size_t... Is>
    bool any_at_end(std::index_sequence<Is...>) const {
        return ((std::get<Is>(its_) == std::get<Is>(ends_)) || ...);
    }
    template<std::size_t... Is>
    std::tuple<val_or_ref_t<Ts>...> deref_and_advance(std::index_sequence<Is...>) {
        return std::tuple<val_or_ref_t<Ts>...>{*std::get<Is>(its_)++...};
    }
public:
    explicit zip_direct_iter(Containers&... cs)
        : its_(cs.begin()...), ends_(cs.end()...) {}

    std::expected<std::tuple<val_or_ref_t<Ts>...>, StopIteration> __next__() {
        if (any_at_end(std::index_sequence_for<Containers...>{}))
            return tpy::make_unexpected(StopIteration{});
        return deref_and_advance(std::index_sequence_for<Containers...>{});
    }

    zip_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const zip_direct_iter&) {
        return os << "<zip>";
    }
};

// lvalue factory: direct iteration for containers, __iter__ fallback otherwise
template<typename... Ts, typename... Cs>
auto builtin_zip(Cs&... cs) {
    if constexpr ((detail::has_begin_end<Cs> && ...)) {
        return zip_direct_iter<zip_types<Ts...>, Cs...>(cs...);
    } else {
        // Braces: __iter__ is observable, and only a braced list evaluates
        // left to right.
        return zip_iter<zip_types<Ts...>, detail::iter_member_t<decltype(tpy::__iter__(cs))>...>{tpy::__iter__(cs)...};
    }
}

// Mixed factory (at least one rvalue): per-arg own (rvalue) or borrow (lvalue).
// std::tuple natively supports reference members, so lvalue args store as C&
// (zero-copy borrow) while rvalue args store as C (moved in).
template<typename... Ts, typename... Cs>
    requires ((!std::is_lvalue_reference_v<Cs&&>) || ...)
auto builtin_zip(Cs&&... cs) {
    return owning_zip_iter<zip_types<Ts...>,
        std::conditional_t<std::is_lvalue_reference_v<Cs&&>,
                           std::remove_reference_t<Cs>&,
                           std::remove_cvref_t<Cs>>...>(
        std::forward<Cs>(cs)...);
}


// -- reversed --

template<typename T, typename Seq>
class reversed_iter : public next_iter_mixin<reversed_iter<T, Seq>, T> {
    const Seq& seq_;
    int32_t index_;
public:
    reversed_iter(const Seq& seq) : seq_(seq), index_(tpy::__len__(seq) - 1) {}

    std::expected<T, StopIteration> __next__() {
        if (index_ < 0) return tpy::make_unexpected(StopIteration{});
        return tpy::__getitem__(seq_, index_--);
    }

    reversed_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const reversed_iter&) {
        return os << "<reversed>";
    }
};

// Owning variant for rvalue sequences.
template<typename T, typename Seq>
class owning_reversed_iter : public next_iter_mixin<owning_reversed_iter<T, Seq>, T> {
    Seq owned_;
    int32_t index_;
public:
    owning_reversed_iter(Seq&& seq) : owned_(std::move(seq)), index_(tpy::__len__(owned_) - 1) {}

    // An index into the owned sequence: nothing points into it, so it moves
    // at any time. Never copied -- a copy would duplicate the sequence.
    owning_reversed_iter(owning_reversed_iter&&) = default;
    owning_reversed_iter(const owning_reversed_iter&) = delete;

    std::expected<T, StopIteration> __next__() {
        if (index_ < 0) return tpy::make_unexpected(StopIteration{});
        return tpy::__getitem__(owned_, index_--);
    }

    owning_reversed_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_reversed_iter&) {
        return os << "<reversed>";
    }
};

// lvalue: store reference (container outlives the loop)
template<typename T, typename Seq>
auto builtin_reversed(const Seq& seq) {
    return reversed_iter<T, Seq>(seq);
}

// rvalue: own the container
template<typename T, typename Seq>
    requires (!std::is_lvalue_reference_v<Seq&&>)
auto builtin_reversed(Seq&& seq) {
    return owning_reversed_iter<T, std::remove_cvref_t<Seq>>(std::move(seq));
}


// -- map --

template<typename U, typename Iter, typename Fn>
class map_iter : public next_iter_mixin<map_iter<U, Iter, Fn>, U> {
    Iter iter_;
    Fn fn_;
public:
    template<typename It>
        requires std::constructible_from<Iter, It&&>
    map_iter(It&& iter, Fn fn)
        : iter_(std::forward<It>(iter)), fn_(std::move(fn)) {}

    std::expected<U, StopIteration> __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return fn_(unwrap_ref(*r));
    }

    map_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const map_iter&) {
        return os << "<map>";
    }
};

// Owning variant: moves the container in so rvalue arguments don't dangle.
template<typename U, typename Container, typename Fn>
class owning_map_iter
    : public next_iter_mixin<owning_map_iter<U, Container, Fn>, U> {
    detail::owned_iter_source<Container> src_;
    Fn fn_;
public:
    owning_map_iter(Fn fn, Container&& c)
        : src_(std::move(c)), fn_(std::move(fn)) { src_.start(); }

    std::expected<U, StopIteration> __next__() {
        auto r = src_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return fn_(unwrap_ref(*r));
    }

    owning_map_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_map_iter&) {
        return os << "<map>";
    }
};

// lvalue: iterate via __iter__/__next__() protocol.
// T is the input element type -- unused in the body but required by the codegen
// template syntax (::tpy::builtin_map<{T}, {U}>).
template<typename T, typename U, typename Fn, typename Iterable>
auto builtin_map(Fn&& fn, Iterable& iterable) {
    return map_iter<U, detail::iter_member_t<decltype(tpy::__iter__(iterable))>, std::decay_t<Fn>>(
        tpy::__iter__(iterable), std::forward<Fn>(fn));
}

// rvalue: own the container to prevent dangling iterators
template<typename T, typename U, typename Fn, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_map(Fn&& fn, Iterable&& iterable) {
    return owning_map_iter<U, std::remove_cvref_t<Iterable>, std::decay_t<Fn>>(
        std::forward<Fn>(fn), std::move(iterable));
}


// -- map_multi (N-iterable, variadic) --

template<typename U, typename Fn, typename IterTuple>
class map_multi_iter : public next_iter_mixin<map_multi_iter<U, Fn, IterTuple>, U> {
    IterTuple iters_;
    Fn fn_;

    template<size_t... Is>
    std::expected<U, StopIteration> call_next(std::index_sequence<Is...>) {
        // Brace-init guarantees left-to-right evaluation order
        auto results = std::tuple{std::get<Is>(iters_).__next__()...};
        if ((!std::get<Is>(results).has_value() || ...))
            return tpy::make_unexpected(StopIteration{});
        // static_cast forwards Own[T] args (Point&&) as rvalues, others as lvalues
        return fn_(static_cast<detail::fn_param_t<Fn, Is>>(unwrap_ref(*std::get<Is>(results)))...);
    }

public:
    map_multi_iter(IterTuple&& iters, Fn fn)
        : iters_(std::move(iters)), fn_(std::move(fn)) {}

    std::expected<U, StopIteration> __next__() {
        return call_next(std::make_index_sequence<std::tuple_size_v<IterTuple>>{});
    }

    map_multi_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const map_multi_iter&) {
        return os << "<map>";
    }
};

template<typename U, typename Fn, typename... Containers>
class owning_map_multi_iter
    : public next_iter_mixin<owning_map_multi_iter<U, Fn, Containers...>, U> {
    std::tuple<detail::arg_source_t<Containers>...> srcs_;
    Fn fn_;

    template<size_t... Is>
    std::expected<U, StopIteration> call_next(std::index_sequence<Is...>) {
        auto results = std::tuple{std::get<Is>(srcs_).__next__()...};
        if ((!std::get<Is>(results).has_value() || ...))
            return tpy::make_unexpected(StopIteration{});
        return fn_(static_cast<detail::fn_param_t<Fn, Is>>(unwrap_ref(*std::get<Is>(results)))...);
    }

public:
    template<typename... CCs>
    owning_map_multi_iter(Fn fn, CCs&&... cs)
        : srcs_(std::forward<CCs>(cs)...), fn_(std::move(fn)) {
        std::apply([](auto&... s) { (s.start(), ...); }, srcs_);
    }

    // Spelled: std::tuple reports itself movable whatever it holds.
    owning_map_multi_iter(owning_map_multi_iter&&)
        requires (std::move_constructible<detail::arg_source_t<Containers>> && ...)
        = default;

    std::expected<U, StopIteration> __next__() {
        return call_next(std::index_sequence_for<Containers...>{});
    }

    owning_map_multi_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_map_multi_iter&) {
        return os << "<map>";
    }
};

// Variadic factory: lvalue (all iterables are lvalue references)
template<typename U, typename Fn, typename... Its>
auto builtin_map_n(Fn&& fn, Its&... its) {
    // Spelled, not deduced: CTAD would decay a self-iterator's `Self&` into a
    // copy. Braces keep the observable __iter__ calls left to right.
    using Iters = std::tuple<detail::iter_member_t<decltype(tpy::__iter__(its))>...>;
    return map_multi_iter<U, std::decay_t<Fn>, Iters>(
        Iters{tpy::__iter__(its)...}, std::forward<Fn>(fn));
}

// Variadic factory: at least one iterable is a temporary. Per argument, own
// (temporary) or borrow (lvalue), like builtin_zip.
template<typename U, typename Fn, typename... Its>
    requires ((!std::is_lvalue_reference_v<Its&&>) || ...)
auto builtin_map_n(Fn&& fn, Its&&... its) {
    return owning_map_multi_iter<U, std::decay_t<Fn>,
        std::conditional_t<std::is_lvalue_reference_v<Its&&>,
                           std::remove_reference_t<Its>&,
                           std::remove_cvref_t<Its>>...>(
        std::forward<Fn>(fn), std::forward<Its>(its)...);
}


// -- filter --

template<typename T, typename Iter, typename Fn>
class filter_iter : public next_iter_mixin<filter_iter<T, Iter, Fn>, T> {
    Iter iter_;
    Fn fn_;
public:
    template<typename U>
        requires std::constructible_from<Iter, U&&>
    filter_iter(U&& iter, Fn fn)
        : iter_(std::forward<U>(iter)), fn_(std::move(fn)) {}

    std::expected<T, StopIteration> __next__() {
        while (true) {
            auto r = iter_.__next__();
            if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
            auto&& elem = unwrap_ref(*r);
            if (fn_(elem)) {
                return elem;
            }
        }
    }

    filter_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const filter_iter&) {
        return os << "<filter>";
    }
};

// Owning variant for rvalue iterables (protocol path).
template<typename T, typename Container, typename Fn>
class owning_filter_iter
    : public next_iter_mixin<owning_filter_iter<T, Container, Fn>, T> {
    detail::owned_iter_source<Container> src_;
    Fn fn_;
public:
    owning_filter_iter(Fn fn, Container&& c)
        : src_(std::move(c)), fn_(std::move(fn)) { src_.start(); }

    std::expected<T, StopIteration> __next__() {
        while (true) {
            auto r = src_.__next__();
            if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
            auto&& elem = unwrap_ref(*r);
            if (fn_(elem)) {
                return elem;
            }
        }
    }

    owning_filter_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_filter_iter&) {
        return os << "<filter>";
    }
};

// Direct-iteration variant: uses C++ begin/end for reference-preserving filter.
// Returns val_or_ref<T> so non-value types yield references into the container.
template<typename T, typename Container, typename Fn>
class filter_direct_iter
    : public next_iter_mixin<filter_direct_iter<T, Container, Fn>, val_or_ref<T>> {
    using CppIter = decltype(std::declval<Container&>().begin());
    CppIter it_;
    CppIter end_;
    Fn fn_;
public:
    filter_direct_iter(Fn fn, Container& c)
        : it_(c.begin()), end_(c.end()), fn_(std::move(fn)) {}

    std::expected<val_or_ref<T>, StopIteration> __next__() {
        while (it_ != end_) {
            auto& elem = *it_;
            ++it_;
            if (fn_(elem)) {
                return val_or_ref<T>(elem);
            }
        }
        return tpy::make_unexpected(StopIteration{});
    }

    filter_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const filter_direct_iter&) {
        return os << "<filter>";
    }
};

// Owning direct-iteration variant for rvalue containers.
template<typename T, typename Container, typename Fn>
class owning_filter_direct_iter
    : public next_iter_mixin<owning_filter_direct_iter<T, Container, Fn>, val_or_ref<T>> {
    detail::owned_range_source<Container> src_;
    Fn fn_;
public:
    owning_filter_direct_iter(Fn fn, Container&& c)
        : src_(std::move(c)), fn_(std::move(fn)) {}

    std::expected<val_or_ref<T>, StopIteration> __next__() {
        while (!src_.done()) {
            auto& elem = src_.take();
            if (fn_(elem)) {
                return val_or_ref<T>(elem);
            }
        }
        return tpy::make_unexpected(StopIteration{});
    }

    owning_filter_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_filter_direct_iter&) {
        return os << "<filter>";
    }
};

// lvalue: direct iteration for containers (preserves references),
// __iter__/__next__() fallback for TPy iterators
template<typename T, typename Fn, typename Iterable>
auto builtin_filter(Fn&& fn, Iterable& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return filter_direct_iter<T, Iterable, std::decay_t<Fn>>(
            std::forward<Fn>(fn), iterable);
    } else {
        return filter_iter<T, detail::iter_member_t<decltype(tpy::__iter__(iterable))>, std::decay_t<Fn>>(
            tpy::__iter__(iterable), std::forward<Fn>(fn));
    }
}

// rvalue: own the container to prevent dangling iterators
template<typename T, typename Fn, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_filter(Fn&& fn, Iterable&& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return owning_filter_direct_iter<T, std::remove_cvref_t<Iterable>,
                                         std::decay_t<Fn>>(
            std::forward<Fn>(fn), std::move(iterable));
    } else {
        return owning_filter_iter<T, std::remove_cvref_t<Iterable>,
                                  std::decay_t<Fn>>(
            std::forward<Fn>(fn), std::move(iterable));
    }
}

// -- filter(None, ...) -- truthy filtering via to_bool

template<typename T, typename Iterable>
auto builtin_filter_truthy(Iterable& iterable) {
    auto pred = [](const auto& x) -> bool { return to_bool(x); };
    return builtin_filter<T>(pred, iterable);
}

template<typename T, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_filter_truthy(Iterable&& iterable) {
    auto pred = [](const auto& x) -> bool { return to_bool(x); };
    return builtin_filter<T>(pred, std::move(iterable));
}

} // namespace tpy
