/**
 * TurboPython Runtime - OwnIter
 *
 * Drain iterator for heap-backed containers (std::vector). Owns a moved
 * vector and yields elements by move. O(1) construction (vector move is
 * pointer swap). Unconsumed elements are destructed when OwnIter is dropped.
 *
 * Implements both C++ range (begin/end with move iterators) and the
 * TurboPython Iterator protocol (__next__/__iter__).
 */

#pragma once

#include "core.hpp"

#include <expected>
#include <iterator>
#include <vector>

namespace tpy {

template<typename T>
struct OwnIter {
    std::vector<T> data;
    std::size_t pos = 0;

    explicit OwnIter(std::vector<T>&& v) : data(std::move(v)) {}

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

// Factory: create OwnIter from an rvalue vector.
template<typename T>
OwnIter<T> own_iter(std::vector<T>&& v) {
    return OwnIter<T>{std::move(v)};
}

} // namespace tpy
