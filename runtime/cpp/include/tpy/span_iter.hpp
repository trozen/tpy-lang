/**
 * TurboPython Runtime - SpanIter
 *
 * Lightweight iterator over a contiguous span. Wraps std::span<T> and exposes
 * both C++ range (begin/end) and OptIterator (__next_opt__) interfaces.
 *
 * SpanIter<T> holds span<T> and yields T& (mutable elements).
 * SpanIter<const T> holds span<const T> and yields const T& (readonly elements).
 * Use SpanIter<const T> when iterating from a const/readonly context.
 */

#pragma once

#include <optional>
#include <span>
#include <type_traits>

namespace tpy {

template<typename T>
struct SpanIter {
    std::span<T> span_;
    size_t index_ = 0;

    explicit SpanIter(std::span<T> s) : span_(s) {}

    // NativeIterable: zero-cost iteration via begin/end.
    // Note: begin()/end() and __next_opt__() track position independently.
    // This is correct for single-consumption patterns (create, iterate, discard)
    // but means mixing both paths on the same instance gives wrong results.
    auto begin() const { return span_.begin() + index_; }
    auto end() const { return span_.end(); }

    // OptIterator protocol -- yields copies of (possibly const) elements
    std::optional<std::remove_const_t<T>> __next_opt__() {
        if (index_ >= span_.size()) return std::nullopt;
        return std::optional<std::remove_const_t<T>>{span_[index_++]};
    }

    // Iterator protocol
    const SpanIter& __iter__() const { return *this; }
};

} // namespace tpy
