/**
 * TurboPython Runtime - SpanIter
 *
 * Lightweight iterator over a contiguous span. Wraps std::span<T> and exposes
 * both C++ range (begin/end) and Iterator (__next__/__iter__) interfaces.
 *
 * SpanIter<T> holds span<T> and yields T& (mutable elements).
 * SpanIter<const T> holds span<const T> and yields const T& (readonly elements).
 * Use SpanIter<const T> when iterating from a const/readonly context.
 */

#pragma once

#include "core.hpp"

#include <expected>
#include <span>
#include <type_traits>

namespace tpy {

template<typename T>
struct SpanIter {
    std::span<T> span_;
    size_t index_ = 0;

    explicit SpanIter(std::span<T> s) : span_(s) {}

    // NativeIterable: zero-cost iteration via begin/end.
    auto begin() const { return span_.begin() + index_; }
    auto end() const { return span_.end(); }

    // Iterator protocol
    std::expected<std::remove_const_t<T>, StopIteration> __next__() {
        if (index_ >= span_.size()) return tpy::make_unexpected(StopIteration{});
        return span_[index_++];
    }

    const SpanIter& __iter__() const { return *this; }
};

} // namespace tpy
