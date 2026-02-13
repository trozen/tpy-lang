/**
 * TurboPython Runtime - Range
 *
 * Python-style range() as a lazy iterator producing values of type T.
 * Depends on: core.hpp (tpy_panic), int32.hpp (int32_add)
 */

#pragma once

#include <cstdint>
#include <iostream>
#include <optional>
#include <type_traits>

namespace tpy {

template<typename T>
class Range {
    T start_;
    T current_;
    T end_;
    T step_;
public:
    Range(T end) : start_{}, current_{}, end_(std::move(end)), step_(1) {}
    Range(T start, T end) : start_(start), current_(std::move(start)), end_(std::move(end)), step_(1) {}
    Range(T start, T end, T step) : start_(start), current_(std::move(start)), end_(std::move(end)), step_(std::move(step)) {
        if (step_ == T{}) tpy_panic("range() arg 3 must not be zero");
    }

    std::optional<T> __next_opt__() {
        if (step_ > T{} ? current_ < end_ : current_ > end_) {
            T val = current_;
            if constexpr (std::is_same_v<T, int32_t>) {
                current_ = int32_add(current_, step_);
            } else {
                current_ += step_;
            }
            return val;
        }
        return std::nullopt;
    }

    friend std::ostream& operator<<(std::ostream& os, const Range& r) {
        os << "range(" << r.start_ << ", " << r.end_;
        if (r.step_ != T{1}) os << ", " << r.step_;
        return os << ")";
    }
};

} // namespace tpy
