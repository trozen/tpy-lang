/**
 * TurboPython Runtime - Iterator Builtins
 *
 * enumerate(), zip(), and reversed() implementations.
 * Depends on: next_iter.hpp, dunder.hpp (for __iter__, __len__, __getitem__)
 */

#pragma once

#include "next_iter.hpp"
#include "dunder.hpp"
#include "type_traits.hpp"

#include <cstdint>
#include <expected>
#include <optional>
#include <tuple>

namespace tpy {

// -- enumerate --

template<typename T, typename Iter>
class enumerate_iter : public next_iter_mixin<enumerate_iter<T, Iter>, std::tuple<int32_t, T>> {
    Iter iter_;
    int32_t index_;  // TPy uses Int32; Python enumerate uses arbitrary-precision
public:
    enumerate_iter(Iter&& iter, int32_t start = 0) : iter_(std::move(iter)), index_(start) {}

    auto __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return decltype(this->_make_result(r))(tpy::make_unexpected(StopIteration{}));
        return _make_result(r);
    }

private:
    template<typename R>
    auto _make_result(R& r) {
        if constexpr (std::is_pointer_v<typename R::value_type>) {
            // Inner iterator returned a pointer -- dereference to get T&
            return std::expected<std::tuple<int32_t, T&>, StopIteration>(
                std::tuple<int32_t, T&>{index_++, *(*r)});
        } else {
            return std::expected<std::tuple<int32_t, T>, StopIteration>(
                std::tuple<int32_t, T>{index_++, std::move(*r)});
        }
    }

public:

    enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// Owning variant: moves the container in so rvalue arguments don't dangle.
template<typename T, typename Container>
class owning_enumerate_iter
    : public next_iter_mixin<owning_enumerate_iter<T, Container>, std::tuple<int32_t, T>> {
    using Iter = decltype(tpy::__iter__(std::declval<Container&>()));
    Container owned_;
    Iter iter_;
    int32_t index_;
public:
    owning_enumerate_iter(Container&& c, int32_t start = 0)
        : owned_(std::move(c)), iter_(tpy::__iter__(owned_)), index_(start) {}

    // iter_ points into owned_; moving would invalidate it (e.g. for std::array).
    // Rely on guaranteed copy elision from the factory functions.
    owning_enumerate_iter(owning_enumerate_iter&&) = delete;
    owning_enumerate_iter& operator=(owning_enumerate_iter&&) = delete;

    auto __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return decltype(this->_make_result(r))(tpy::make_unexpected(StopIteration{}));
        return _make_result(r);
    }

private:
    template<typename R>
    auto _make_result(R& r) {
        if constexpr (std::is_pointer_v<typename R::value_type>) {
            return std::expected<std::tuple<int32_t, T&>, StopIteration>(
                std::tuple<int32_t, T&>{index_++, *(*r)});
        } else {
            return std::expected<std::tuple<int32_t, T>, StopIteration>(
                std::tuple<int32_t, T>{index_++, std::move(*r)});
        }
    }

public:
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
    using CppIter = decltype(std::declval<Container&>().begin());
    Container owned_;
    CppIter it_;
    CppIter end_;
    int32_t index_;
public:
    owning_enumerate_direct_iter(Container&& c, int32_t start = 0)
        : owned_(std::move(c)), it_(owned_.begin()), end_(owned_.end()), index_(start) {}

    owning_enumerate_direct_iter(owning_enumerate_direct_iter&&) = delete;
    owning_enumerate_direct_iter& operator=(owning_enumerate_direct_iter&&) = delete;

    std::expected<std::tuple<int32_t, val_or_ref_t<T>>, StopIteration> __next__() {
        if (it_ == end_) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, val_or_ref_t<T>>{index_++, *it_++};
    }

    owning_enumerate_direct_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_enumerate_direct_iter&) {
        return os << "<enumerate>";
    }
};

namespace detail {
template<typename T>
concept has_begin_end = requires(T& t) { t.begin(); t.end(); };
}

// lvalue: direct iteration for containers (preserves references),
// __iter__/__next__() fallback for TPy iterators
template<typename T, typename Iterable>
auto builtin_enumerate(Iterable& iterable) {
    if constexpr (detail::has_begin_end<Iterable>) {
        return enumerate_direct_iter<T, Iterable>(iterable);
    } else {
        auto iter = tpy::__iter__(iterable);
        return enumerate_iter<T, decltype(iter)>(std::move(iter));
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
        auto iter = tpy::__iter__(iterable);
        return enumerate_iter<T, decltype(iter)>(std::move(iter), start);
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
template<typename Tuple, typename... Opts, std::size_t... Is>
bool zip_advance(Tuple& iters, std::tuple<Opts...>& opts, std::index_sequence<Is...>) {
    bool ok = true;
    // && short-circuits: once ok is false, remaining lambdas are not called.
    // Matches Python zip() -- stop at shortest without over-advancing.
    ((ok = ok && [&]{
        auto r = std::get<Is>(iters).__next__();
        if (!r.has_value()) return false;
        std::get<Is>(opts).emplace(std::move(*r));
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
    explicit zip_iter(Iters&&... iters) : iters_(std::move(iters)...) {}

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
    std::tuple<Containers...> owned_;
    std::tuple<decltype(tpy::__iter__(std::declval<Containers&>()))...> iters_;

    template<std::size_t... Is>
    auto make_iters(std::index_sequence<Is...>) {
        return std::tuple{tpy::__iter__(std::get<Is>(owned_))...};
    }
public:
    template<typename... Us>
    explicit owning_zip_iter(Us&&... cs)
        : owned_(std::forward<Us>(cs)...),
          iters_(make_iters(std::index_sequence_for<Containers...>{})) {}

    owning_zip_iter(owning_zip_iter&&) = delete;
    owning_zip_iter& operator=(owning_zip_iter&&) = delete;

    std::expected<std::tuple<Ts...>, StopIteration> __next__() {
        std::tuple<std::optional<Ts>...> opts;
        if (!detail::zip_advance(iters_, opts, std::index_sequence_for<Containers...>{}))
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
        return zip_iter<zip_types<Ts...>, decltype(tpy::__iter__(cs))...>(tpy::__iter__(cs)...);
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

    owning_reversed_iter(owning_reversed_iter&&) = delete;
    owning_reversed_iter& operator=(owning_reversed_iter&&) = delete;

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

} // namespace tpy
