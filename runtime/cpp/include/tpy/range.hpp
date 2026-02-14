/**
 * TurboPython Runtime - Range
 *
 * Python-style range() as an immutable, reusable container with begin/end.
 * Depends on: core.hpp (tpy_panic), fixed_int.hpp
 */

#pragma once

#include <cstdint>
#include <iostream>
#include <limits>
#include <type_traits>
#include "fixed_int.hpp"

namespace tpy {

// Upfront overflow check for range loops with fixed-width integer types.
// Verifies that stepping through [start, stop) with the given step will never
// overflow T, so the loop body can use unchecked += for maximum speed.
// For step ±1 this is unnecessary (stop is already a valid T value).
// All arithmetic uses unsigned to avoid signed overflow UB in the check itself.
//
// The check computes the exact exit value (the value of i after the final
// increment) and verifies it fits in T.  When step evenly divides
// (stop - start), the exit value is exactly `stop` (always representable).
// Otherwise the exit value overshoots by (step - remainder), and we check
// that overshoot against T's bounds.
template<typename T>
void range_check_overflow(T start, T stop, T step) {
    static_assert(std::is_integral_v<T> && sizeof(T) <= 8);
    using U = std::make_unsigned_t<T>;
    if (step > T{0}) {
        if (start >= stop) return;  // empty range
        U d = static_cast<U>(stop) - static_cast<U>(start);
        U ustep = static_cast<U>(step);
        U rem = d % ustep;
        if (rem == 0) return;  // exit value is exactly stop, always safe
        // Exit value = stop + (step - rem).  Need: stop + (step - rem) <= T::max.
        U overshoot = ustep - rem;
        U room = static_cast<U>(std::numeric_limits<T>::max()) - static_cast<U>(stop);
        if (overshoot > room) {
            tpy_panic("range() would overflow on iteration");
        }
    } else if constexpr (std::is_signed_v<T>) {
        if (step < T{0}) {
            if (start <= stop) return;  // empty range
            U d = static_cast<U>(start) - static_cast<U>(stop);
            U abs_step = static_cast<U>(0) - static_cast<U>(step);
            U rem = d % abs_step;
            if (rem == 0) return;  // exit value is exactly stop, always safe
            // Exit value = stop - (abs_step - rem).  Need: stop - (abs_step - rem) >= T::min.
            U overshoot = abs_step - rem;
            U room = static_cast<U>(stop) - static_cast<U>(std::numeric_limits<T>::min());
            if (overshoot > room) {
                tpy_panic("range() would overflow on iteration");
            }
        }
    }
}

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
        if constexpr (std::is_integral_v<T> && sizeof(T) <= 8)
            range_check_overflow<T>(start_, end_, step_);
    }

    struct Iterator {
        using value_type = T;
        using difference_type = std::ptrdiff_t;
        using iterator_category = std::input_iterator_tag;
        T current_, end_, step_;
        T operator*() const { return current_; }
        Iterator& operator++() {
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
