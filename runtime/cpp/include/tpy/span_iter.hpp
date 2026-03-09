/**
 * TurboPython Runtime - SpanIter
 *
 * Lightweight iterator over a contiguous span. Wraps std::span<const T>
 * and exposes both C++ range (begin/end) and OptIterator (__next_opt__)
 * interfaces.
 */

#pragma once

#include <optional>
#include <span>

namespace tpy {

template<typename T>
struct SpanIter {
    std::span<const T> span_;
    size_t index_ = 0;

    explicit SpanIter(std::span<const T> s) : span_(s) {}

    // NativeIterable: zero-cost iteration via begin/end.
    // Note: begin()/end() and __next_opt__() track position independently.
    // This is correct for single-consumption patterns (create, iterate, discard)
    // but means mixing both paths on the same instance gives wrong results.
    auto begin() const { return span_.begin() + index_; }
    auto end() const { return span_.end(); }

    // OptIterator protocol
    std::optional<T> __next_opt__() {
        if (index_ >= span_.size()) return std::nullopt;
        return std::optional<T>{span_[index_++]};
    }

    // Iterator protocol
    const SpanIter& __iter__() const { return *this; }
};

} // namespace tpy
