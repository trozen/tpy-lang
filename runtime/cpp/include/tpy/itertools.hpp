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

template<typename T, typename Iterable>
auto builtin_enumerate(Iterable&& iterable) {
    auto iter = tpy::__iter__(iterable);
    return enumerate_iter<T, decltype(iter)>(std::move(iter));
}

template<typename T, typename Iterable>
auto builtin_enumerate_start(Iterable&& iterable, int32_t start) {
    auto iter = tpy::__iter__(iterable);
    return enumerate_iter<T, decltype(iter)>(std::move(iter), start);
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

template<typename T, typename Seq>
auto builtin_reversed(const Seq& seq) {
    return reversed_iter<T, Seq>(seq);
}

// Prevent binding to temporaries (would dangle since reversed_iter stores a reference)
template<typename T, typename Seq>
    requires (!std::is_lvalue_reference_v<Seq&&>)
auto builtin_reversed(Seq&&) = delete;

} // namespace tpy
