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

    // A pinned holder has no eligible copy or move constructor, and the
    // calling convention may then return a small one in registers -- a
    // bitwise relocation that leaves the cursor pointing into the callee's
    // dead frame. A non-trivial destructor forces the return through memory,
    // where guaranteed elision builds it in its final place.
    ~owned_iter_source() requires separate_user_iterator<C> {}
    ~owned_iter_source() = default;

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

// Per-argument holder of a mixed combinator: X is `C&` for an lvalue argument
// and `C` for a temporary.
template<typename X>
using arg_source_t = std::conditional_t<
    std::is_lvalue_reference_v<X>,
    borrowed_iter_source<std::remove_reference_t<X>>,
    owned_iter_source<X>>;

// -- The form a combinator hands an element on --
//
// Spelled off the SOURCE, never off a type the compiler names: an element the
// source lends is lent on, with the source's const (an element of a
// `const std::vector<Cell>&` is a `const Cell&`), and an element the source
// builds is carried by value, since there is nothing to point at. A value
// type is always carried by value, which is what a `for` binds anyway.

// What a `__next__()` step carries.
template<typename S>
using step_value_t = typename decltype(std::declval<S&>().__next__())::value_type;

// A step's element as a MEMBER of a combinator's tuple. A lent element
// (`val_or_ref<X>`) becomes the reference `X&`, const included; anything
// else -- a fresh value, a nested combinator's tuple -- is carried as it came.
template<typename V>
struct step_member { using type = V; };
template<typename X>
struct step_member<val_or_ref<X>> {
    using type = std::conditional_t<val_or_ref<X>::is_val, std::remove_const_t<X>, X&>;
};
// A nested combinator's tuple step is handed on as `unwrap_ref_move` hands it
// on: its lent members stay lent and an OWNED member (an inner owning flavor's
// `X&&`) is moved into the outer tuple, so nothing points at the inner step
// once it is gone.
template<typename... Ts>
struct step_member<std::tuple<Ts...>> {
    using type = decltype(tpy::unwrap_ref_move(std::declval<std::tuple<Ts...>&>()));
};
template<typename S>
using step_member_t = typename step_member<step_value_t<S>>::type;

// What a begin()/end() source's iterator dereferences to. A proxy prvalue
// (`std::vector<bool>`) stands for its iterator's value type.
template<typename It>
using deref_t = decltype(*std::declval<It&>());
template<typename It>
struct prvalue_elem { using type = std::remove_cvref_t<deref_t<It>>; };
template<typename It>
    requires requires { typename std::iter_value_t<It>; }
struct prvalue_elem<It> { using type = std::iter_value_t<It>; };

template<typename It>
struct range_elem {
    using ref = deref_t<It>;
    static constexpr bool lent =
        std::is_lvalue_reference_v<ref> && !is_value_type_v<std::remove_cvref_t<ref>>;
    // As a tuple member: the reference itself, or the value.
    using member = std::conditional_t<
        lent, ref,
        std::conditional_t<std::is_lvalue_reference_v<ref>,
                           std::remove_cvref_t<ref>, typename prvalue_elem<It>::type>>;
    // As a step result: an `expected` cannot hold a reference, so a lent
    // element rides in `val_or_ref` (a `const X` payload keeps the const).
    using step = std::conditional_t<lent, val_or_ref<std::remove_reference_t<ref>>, member>;
};
template<typename C>
using range_member_t = typename range_elem<begin_iter_t<C>>::member;
template<typename C>
using range_step_t = typename range_elem<begin_iter_t<C>>::step;

// A member an OWNING combinator lends out of a source it holds: an rvalue
// reference, so a collect moves the element out of the container that dies
// with the combinator, while a `for` still binds it as an lvalue. A const
// element is only ever copied. Only the TUPLE flavors carry this form: a
// single-element step rides in `val_or_ref`, which lends or owns a copy but
// cannot say "owned reference", so a collect over `filter(f, make())` copies
// where one over `enumerate(make())` moves. Owning the source OBJECT is not owning its
// ELEMENTS: a borrowed range held by value (a `std::span`, a dict view)
// points at a caller's container, and its elements stay lent -- the
// standard's `enable_borrowed_range` is the fact consulted.
template<typename M, typename C>
using owned_member_t = std::conditional_t<
    std::is_lvalue_reference_v<M> && !std::is_const_v<std::remove_reference_t<M>>
        && !std::ranges::enable_borrowed_range<std::remove_cvref_t<C>>,
    std::remove_reference_t<M>&&, M>;

// Hand `v` on as tuple member `M`: an rvalue-reference member takes the
// element's value, anything else the reference or value as it came.
template<typename M, typename V>
decltype(auto) as_member(V&& v) {
    if constexpr (std::is_rvalue_reference_v<M>) return std::move(v);
    else return std::forward<V>(v);
}

// The member an owned source hands on. A CONTAINER the combinator holds
// lends into storage that dies with the combinator, so its member is owned;
// a self-iterator it holds (a nested combinator, a user iterator) lends from
// wherever it points, so its step is handed on as it came.
template<typename C>
using owned_source_member_t = std::conditional_t<
    is_self_iterator_v<C>,
    step_member_t<owned_iter_source<C>>,
    owned_member_t<step_member_t<owned_iter_source<C>>, C>>;

// A value `__getitem__` hands back, as a step result.
template<typename R>
using result_step_t = std::conditional_t<
    std::is_lvalue_reference_v<R> && !is_value_type_v<std::remove_cvref_t<R>>,
    val_or_ref<std::remove_reference_t<R>>, std::remove_cvref_t<R>>;

} // namespace detail

// -- enumerate --

template<typename Iter>
class enumerate_iter
    : public next_iter_mixin<enumerate_iter<Iter>,
                             std::tuple<int32_t, detail::step_member_t<Iter>>> {
    using elem_t = detail::step_member_t<Iter>;
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

    std::expected<std::tuple<int32_t, elem_t>, StopIteration> __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, elem_t>{index_++, unwrap_ref_move(*r)};
    }

    enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// Owning variant: moves the container in so rvalue arguments don't dangle.
template<typename Container>
class owning_enumerate_iter
    : public next_iter_mixin<owning_enumerate_iter<Container>,
                             std::tuple<int32_t, detail::owned_source_member_t<Container>>> {
    using elem_t = detail::owned_source_member_t<Container>;
    detail::owned_iter_source<Container> src_;
    int32_t index_;
public:
    owning_enumerate_iter(Container&& c, int32_t start = 0)
        : src_(std::move(c)), index_(start) { src_.start(); }

    std::expected<std::tuple<int32_t, elem_t>, StopIteration> __next__() {
        auto r = src_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, elem_t>{index_++, detail::as_member<elem_t>(unwrap_ref_move(*r))};
    }

    owning_enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// Direct-iteration variant: uses C++ begin/end to get references to container
// elements instead of going through __iter__/__next__() which copies.
template<typename Container>
class enumerate_direct_iter
    : public next_iter_mixin<enumerate_direct_iter<Container>,
                             std::tuple<int32_t, detail::range_member_t<Container>>> {
    using elem_t = detail::range_member_t<Container>;
    using CppIter = begin_iter_t<Container>;
    Container& container_;
    CppIter it_;
    CppIter end_;
    int32_t index_;
public:
    enumerate_direct_iter(Container& c, int32_t start = 0)
        : container_(c), it_(c.begin()), end_(c.end()), index_(start) {}

    std::expected<std::tuple<int32_t, elem_t>, StopIteration> __next__() {
        if (it_ == end_) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, elem_t>{index_++, *it_++};
    }

    enumerate_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const enumerate_direct_iter&) {
        return os << "<enumerate>";
    }
};

// Owning direct-iteration variant for rvalue containers.
template<typename Container>
class owning_enumerate_direct_iter
    : public next_iter_mixin<owning_enumerate_direct_iter<Container>,
                             std::tuple<int32_t, detail::owned_member_t<detail::range_member_t<Container>, Container>>> {
    using elem_t = detail::owned_member_t<detail::range_member_t<Container>, Container>;
    detail::owned_range_source<Container> src_;
    int32_t index_;
public:
    owning_enumerate_direct_iter(Container&& c, int32_t start = 0)
        : src_(std::move(c)), index_(start) {}

    std::expected<std::tuple<int32_t, elem_t>, StopIteration> __next__() {
        if (src_.done()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, elem_t>{index_++, detail::as_member<elem_t>(src_.take())};
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
template<typename Iterable>
auto builtin_enumerate(Iterable& iterable, int32_t start = 0) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return enumerate_direct_iter<Iterable>(iterable, start);
    } else {
        return enumerate_iter<detail::iter_member_t<decltype(tpy::__iter__(iterable))>>(
            tpy::__iter__(iterable), start);
    }
}

// rvalue: own the container to prevent dangling iterators
template<typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_enumerate(Iterable&& iterable, int32_t start = 0) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return owning_enumerate_direct_iter<std::remove_cvref_t<Iterable>>(std::move(iterable), start);
    } else {
        return owning_enumerate_iter<std::remove_cvref_t<Iterable>>(std::move(iterable), start);
    }
}

// -- zip (variadic) --

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
        std::get<Is>(opts).emplace(tpy::unwrap_ref_move(*r));
        return true;
    }()), ...);
    return ok;
}

// `std::optional` cannot hold a reference: a lent member waits in a
// `val_or_ref` between the advance and the collect.
template<typename M>
using zip_slot_t = std::conditional_t<std::is_reference_v<M>,
                                      val_or_ref<std::remove_reference_t<M>>, M>;

// Build the tuple out of the slots: a lent member binds the reference, a
// value or an owned member moves out.
template<typename Tuple, typename Opts, std::size_t... Is>
Tuple zip_collect(Opts& opts, std::index_sequence<Is...>) {
    return Tuple{as_member<std::tuple_element_t<Is, Tuple>>(
        tpy::unwrap_ref_move(*std::get<Is>(opts)))...};
}

// The tuple a set of pulled sources yields, one member per source; a source
// the combinator owns lends its member as owned.
template<typename Src>
struct zip_member { using type = step_member_t<Src>; };
template<typename C>
struct zip_member<owned_iter_source<C>> { using type = owned_source_member_t<C>; };
template<typename... Srcs>
using zip_tuple_t = std::tuple<typename zip_member<Srcs>::type...>;

} // namespace detail

// Primary template (never instantiated directly).
template<typename... Iters>
class zip_iter
    : public next_iter_mixin<zip_iter<Iters...>, detail::zip_tuple_t<Iters...>> {
    using tuple_t = detail::zip_tuple_t<Iters...>;
    std::tuple<Iters...> iters_;
public:
    // forward, not move: a self-iterator argument arrives as `Self&` and is
    // advanced in place. A template because an `Iters` may be a VALUE copied
    // from a const reference (detail::iter_member_t).
    template<typename... Us>
        requires (sizeof...(Us) == sizeof...(Iters))
              && (std::constructible_from<Iters, Us&&> && ...)
    explicit zip_iter(Us&&... iters) : iters_(std::forward<Us>(iters)...) {}

    std::expected<tuple_t, StopIteration> __next__() {
        std::tuple<std::optional<detail::zip_slot_t<typename detail::zip_member<Iters>::type>>...> opts;
        if (!detail::zip_advance(iters_, opts, std::index_sequence_for<Iters...>{}))
            return tpy::make_unexpected(StopIteration{});
        return detail::zip_collect<tuple_t>(opts, std::index_sequence_for<Iters...>{});
    }

    zip_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const zip_iter&) {
        return os << "<zip>";
    }
};

template<typename... Containers>
class owning_zip_iter
    : public next_iter_mixin<owning_zip_iter<Containers...>,
                             detail::zip_tuple_t<detail::arg_source_t<Containers>...>> {
    using tuple_t = detail::zip_tuple_t<detail::arg_source_t<Containers>...>;
    template<typename C>
    using member_t = typename detail::zip_member<detail::arg_source_t<C>>::type;
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

    std::expected<tuple_t, StopIteration> __next__() {
        std::tuple<std::optional<detail::zip_slot_t<member_t<Containers>>>...> opts;
        if (!detail::zip_advance(srcs_, opts, std::index_sequence_for<Containers...>{}))
            return tpy::make_unexpected(StopIteration{});
        return detail::zip_collect<tuple_t>(opts, std::index_sequence_for<Containers...>{});
    }

    owning_zip_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_zip_iter&) {
        return os << "<zip>";
    }
};

// Direct-iteration zip: uses C++ begin/end for reference-preserving iteration.
template<typename... Containers>
class zip_direct_iter
    : public next_iter_mixin<zip_direct_iter<Containers...>,
                             std::tuple<detail::range_member_t<Containers>...>> {
    using tuple_t = std::tuple<detail::range_member_t<Containers>...>;
    std::tuple<begin_iter_t<Containers>...> its_;
    std::tuple<decltype(std::declval<Containers&>().end())...> ends_;

    template<std::size_t... Is>
    bool any_at_end(std::index_sequence<Is...>) const {
        return ((std::get<Is>(its_) == std::get<Is>(ends_)) || ...);
    }
    template<std::size_t... Is>
    tuple_t deref_and_advance(std::index_sequence<Is...>) {
        return tuple_t{*std::get<Is>(its_)++...};
    }
public:
    explicit zip_direct_iter(Containers&... cs)
        : its_(cs.begin()...), ends_(cs.end()...) {}

    std::expected<tuple_t, StopIteration> __next__() {
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
template<typename... Cs>
auto builtin_zip(Cs&... cs) {
    if constexpr ((detail::has_begin_end<Cs> && ...)) {
        return zip_direct_iter<Cs...>(cs...);
    } else {
        // Braces: __iter__ is observable, and only a braced list evaluates
        // left to right.
        return zip_iter<detail::iter_member_t<decltype(tpy::__iter__(cs))>...>{tpy::__iter__(cs)...};
    }
}

// Mixed factory (at least one rvalue): per-arg own (rvalue) or borrow (lvalue).
// std::tuple natively supports reference members, so lvalue args store as C&
// (zero-copy borrow) while rvalue args store as C (moved in).
template<typename... Cs>
    requires ((!std::is_lvalue_reference_v<Cs&&>) || ...)
auto builtin_zip(Cs&&... cs) {
    return owning_zip_iter<
        std::conditional_t<std::is_lvalue_reference_v<Cs&&>,
                           std::remove_reference_t<Cs>&,
                           std::remove_cvref_t<Cs>>...>(
        std::forward<Cs>(cs)...);
}

// -- reversed --

namespace detail {
// What `seq[i]` hands back, as a step result: a container lends its element
// (const included), a `str` builds a `char`.
template<typename Seq>
using getitem_step_t = result_step_t<decltype(tpy::__getitem__(std::declval<Seq&>(), int32_t{}))>;
}

template<typename Seq>
class reversed_iter : public next_iter_mixin<reversed_iter<Seq>, detail::getitem_step_t<Seq>> {
    using step_t = detail::getitem_step_t<Seq>;
    Seq& seq_;
    int32_t index_;
public:
    reversed_iter(Seq& seq) : seq_(seq), index_(tpy::__len__(seq) - 1) {}

    std::expected<step_t, StopIteration> __next__() {
        if (index_ < 0) return tpy::make_unexpected(StopIteration{});
        return step_t(tpy::__getitem__(seq_, index_--));
    }

    reversed_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const reversed_iter&) {
        return os << "<reversed>";
    }
};

// Owning variant for rvalue sequences.
template<typename Seq>
class owning_reversed_iter
    : public next_iter_mixin<owning_reversed_iter<Seq>, detail::getitem_step_t<Seq>> {
    using step_t = detail::getitem_step_t<Seq>;
    Seq owned_;
    int32_t index_;
public:
    owning_reversed_iter(Seq&& seq) : owned_(std::move(seq)), index_(tpy::__len__(owned_) - 1) {}

    // An index into the owned sequence: nothing points into it, so it moves
    // at any time. Never copied -- a copy would duplicate the sequence.
    owning_reversed_iter(owning_reversed_iter&&) = default;
    owning_reversed_iter(const owning_reversed_iter&) = delete;

    std::expected<step_t, StopIteration> __next__() {
        if (index_ < 0) return tpy::make_unexpected(StopIteration{});
        return step_t(tpy::__getitem__(owned_, index_--));
    }

    owning_reversed_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_reversed_iter&) {
        return os << "<reversed>";
    }
};

// lvalue: store reference (container outlives the loop); `Seq` deduces the
// argument's const, so a const sequence lends const elements.
template<typename Seq>
auto builtin_reversed(Seq& seq) {
    return reversed_iter<Seq>(seq);
}

// rvalue: own the container
template<typename Seq>
    requires (!std::is_lvalue_reference_v<Seq&&>)
auto builtin_reversed(Seq&& seq) {
    return owning_reversed_iter<std::remove_cvref_t<Seq>>(std::move(seq));
}


// -- map --
//
// `U` is what the callable hands back, in the step form the compiler
// declares for it: the callable's signature is the compiler's fact (a borrow
// return is `val_or_ref<T>`, a borrow-form tuple has `val_or_ref` members
// where its C++ spelling has pointers), unlike a source's element, which
// only the source itself can name.

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
template<typename U, typename Fn, typename Iterable>
auto builtin_map(Fn&& fn, Iterable& iterable) {
    return map_iter<U, detail::iter_member_t<decltype(tpy::__iter__(iterable))>, std::decay_t<Fn>>(
        tpy::__iter__(iterable), std::forward<Fn>(fn));
}

// rvalue: own the container to prevent dangling iterators
template<typename U, typename Fn, typename Iterable>
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

// Over an iterator source the step passes through as it came: a lent element
// stays lent, a fresh one moves on.
template<typename Iter, typename Fn>
class filter_iter : public next_iter_mixin<filter_iter<Iter, Fn>, detail::step_value_t<Iter>> {
    using step_t = detail::step_value_t<Iter>;
    Iter iter_;
    Fn fn_;
public:
    template<typename U>
        requires std::constructible_from<Iter, U&&>
    filter_iter(U&& iter, Fn fn)
        : iter_(std::forward<U>(iter)), fn_(std::move(fn)) {}

    std::expected<step_t, StopIteration> __next__() {
        while (true) {
            auto r = iter_.__next__();
            if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
            if (fn_(unwrap_ref(*r))) {
                return std::move(r);
            }
        }
    }

    filter_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const filter_iter&) {
        return os << "<filter>";
    }
};

// Owning variant for rvalue iterables (protocol path).
template<typename Container, typename Fn>
class owning_filter_iter
    : public next_iter_mixin<owning_filter_iter<Container, Fn>,
                             detail::step_value_t<detail::owned_iter_source<Container>>> {
    using step_t = detail::step_value_t<detail::owned_iter_source<Container>>;
    detail::owned_iter_source<Container> src_;
    Fn fn_;
public:
    owning_filter_iter(Fn fn, Container&& c)
        : src_(std::move(c)), fn_(std::move(fn)) { src_.start(); }

    std::expected<step_t, StopIteration> __next__() {
        while (true) {
            auto r = src_.__next__();
            if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
            if (fn_(unwrap_ref(*r))) {
                return std::move(r);
            }
        }
    }

    owning_filter_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_filter_iter&) {
        return os << "<filter>";
    }
};

// Direct-iteration variant: uses C++ begin/end for reference-preserving filter.
template<typename Container, typename Fn>
class filter_direct_iter
    : public next_iter_mixin<filter_direct_iter<Container, Fn>, detail::range_step_t<Container>> {
    using step_t = detail::range_step_t<Container>;
    using CppIter = begin_iter_t<Container>;
    CppIter it_;
    CppIter end_;
    Fn fn_;
public:
    filter_direct_iter(Fn fn, Container& c)
        : it_(c.begin()), end_(c.end()), fn_(std::move(fn)) {}

    std::expected<step_t, StopIteration> __next__() {
        while (it_ != end_) {
            auto&& elem = *it_;
            ++it_;
            if (fn_(elem)) {
                return step_t(elem);
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
template<typename Container, typename Fn>
class owning_filter_direct_iter
    : public next_iter_mixin<owning_filter_direct_iter<Container, Fn>, detail::range_step_t<Container>> {
    using step_t = detail::range_step_t<Container>;
    detail::owned_range_source<Container> src_;
    Fn fn_;
public:
    owning_filter_direct_iter(Fn fn, Container&& c)
        : src_(std::move(c)), fn_(std::move(fn)) {}

    std::expected<step_t, StopIteration> __next__() {
        while (!src_.done()) {
            auto&& elem = src_.take();
            if (fn_(elem)) {
                return step_t(elem);
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
template<typename Fn, typename Iterable>
auto builtin_filter(Fn&& fn, Iterable& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return filter_direct_iter<Iterable, std::decay_t<Fn>>(
            std::forward<Fn>(fn), iterable);
    } else {
        return filter_iter<detail::iter_member_t<decltype(tpy::__iter__(iterable))>, std::decay_t<Fn>>(
            tpy::__iter__(iterable), std::forward<Fn>(fn));
    }
}

// rvalue: own the container to prevent dangling iterators
template<typename Fn, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_filter(Fn&& fn, Iterable&& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return owning_filter_direct_iter<std::remove_cvref_t<Iterable>,
                                         std::decay_t<Fn>>(
            std::forward<Fn>(fn), std::move(iterable));
    } else {
        return owning_filter_iter<std::remove_cvref_t<Iterable>,
                                  std::decay_t<Fn>>(
            std::forward<Fn>(fn), std::move(iterable));
    }
}

// -- filter(None, ...) -- truthy filtering via to_bool

template<typename Iterable>
auto builtin_filter_truthy(Iterable& iterable) {
    auto pred = [](const auto& x) -> bool { return to_bool(x); };
    return builtin_filter(pred, iterable);
}

template<typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_filter_truthy(Iterable&& iterable) {
    auto pred = [](const auto& x) -> bool { return to_bool(x); };
    return builtin_filter(pred, std::move(iterable));
}

} // namespace tpy
