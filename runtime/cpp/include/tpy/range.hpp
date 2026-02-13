/**
 * TurboPython Runtime - Range
 *
 * Python-style range() as an immutable, reusable container with begin/end.
 * Depends on: core.hpp (tpy_panic), int32.hpp (int32_add)
 */

#pragma once

#include <cstdint>
#include <iostream>
#include <type_traits>

namespace tpy {

template<typename T>
class Range {
    T start_;
    T end_;
    T step_;
public:
    Range() : start_{}, end_{}, step_(1) {}
    Range(T end) : start_{}, end_(std::move(end)), step_(1) {}
    Range(T start, T end) : start_(start), end_(std::move(end)), step_(1) {}
    Range(T start, T end, T step) : start_(start), end_(std::move(end)), step_(std::move(step)) {
        if (step_ == T{}) tpy_panic("range() arg 3 must not be zero");
    }

    struct Iterator {
        using value_type = T;
        using difference_type = std::ptrdiff_t;
        using iterator_category = std::input_iterator_tag;
        T current_, end_, step_;
        T operator*() const { return current_; }
        Iterator& operator++() {
            if constexpr (std::is_same_v<T, int32_t>)
                current_ = int32_add(current_, step_);
            else
                current_ += step_;
            if (step_ > T{} ? !(current_ < end_) : !(current_ > end_))
                current_ = end_;
            return *this;
        }
        Iterator operator++(int) { auto t = *this; ++(*this); return t; }
        bool operator==(const Iterator& o) const { return current_ == o.current_; }
        bool operator!=(const Iterator& o) const { return !(current_ == o.current_); }
    };

    Iterator begin() const {
        bool empty = step_ > T{} ? !(start_ < end_) : !(start_ > end_);
        return {empty ? end_ : start_, end_, step_};
    }
    Iterator end() const { return {end_, end_, step_}; }

    friend std::ostream& operator<<(std::ostream& os, const Range& r) {
        os << "range(" << r.start_ << ", " << r.end_;
        if (r.step_ != T{1}) os << ", " << r.step_;
        return os << ")";
    }
};

} // namespace tpy
