/**
 * TurboPython Runtime - Iterator Builtins
 *
 * enumerate() and reversed() implementations.
 * Depends on: next_iter.hpp, dunder.hpp (for __iter__, __len__, __getitem__)
 */

#pragma once

#include "next_iter.hpp"
#include "dunder.hpp"

#include <cstdint>
#include <expected>
#include <tuple>

namespace tpy {

// -- enumerate --

template<typename T, typename Iter>
class enumerate_iter : public next_iter_mixin<enumerate_iter<T, Iter>, std::tuple<int32_t, T>> {
    Iter iter_;
    int32_t index_;  // TPy uses Int32; Python enumerate uses arbitrary-precision
public:
    enumerate_iter(Iter&& iter, int32_t start = 0) : iter_(std::move(iter)), index_(start) {}

    std::expected<std::tuple<int32_t, T>, StopIteration> __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, T>{index_++, std::move(*r)};
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

    std::expected<std::tuple<int32_t, T>, StopIteration> __next__() {
        auto r = iter_.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return std::tuple<int32_t, T>{index_++, std::move(*r)};
    }

    owning_enumerate_iter& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const owning_enumerate_iter&) {
        return os << "<enumerate>";
    }
};

// lvalue: extract iterator (container outlives the loop)
template<typename T, typename Iterable>
auto builtin_enumerate(Iterable& iterable) {
    auto iter = tpy::__iter__(iterable);
    return enumerate_iter<T, decltype(iter)>(std::move(iter));
}

// rvalue: own the container to prevent dangling iterators
template<typename T, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_enumerate(Iterable&& iterable) {
    return owning_enumerate_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable));
}

// lvalue with start
template<typename T, typename Iterable>
auto builtin_enumerate_start(Iterable& iterable, int32_t start) {
    auto iter = tpy::__iter__(iterable);
    return enumerate_iter<T, decltype(iter)>(std::move(iter), start);
}

// rvalue with start
template<typename T, typename Iterable>
    requires (!std::is_lvalue_reference_v<Iterable&&>)
auto builtin_enumerate_start(Iterable&& iterable, int32_t start) {
    return owning_enumerate_iter<T, std::remove_cvref_t<Iterable>>(std::move(iterable), start);
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
