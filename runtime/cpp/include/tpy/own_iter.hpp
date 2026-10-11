/**
 * TurboPython Runtime - OwnIter
 *
 * Drain iterator over a container it owns: a `std::vector` (moving it in is a
 * pointer swap) or a `std::array` (a list literal local kept as an array; moved
 * in element by element). Yields elements by move; unconsumed elements are
 * destructed when OwnIter is dropped.
 *
 * Implements both C++ range (begin/end with move iterators) and the
 * TurboPython Iterator protocol (__next__/__iter__).
 */

#pragma once

#include "core.hpp"

#include <array>
#include <cstddef>
#include <expected>
#include <iterator>
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

// Factory: create OwnIter from an rvalue vector or array.
template<typename T>
OwnIter<T> own_iter(std::vector<T>&& v) {
    return OwnIter<T>{std::move(v)};
}

template<typename T, std::size_t N>
OwnIter<T, std::array<T, N>> own_iter(std::array<T, N>&& a) {
    return OwnIter<T, std::array<T, N>>{std::move(a)};
}

} // namespace tpy
