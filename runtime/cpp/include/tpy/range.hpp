/**
 * TurboPython Runtime - Range
 *
 * Python-style range() as a lazy iterator producing int32_t values.
 * Depends on: core.hpp (tpy_panic), int32.hpp (int32_add)
 */

#pragma once

#include <cstdint>
#include <optional>

namespace tpy {

class Range {
    int32_t current_;
    int32_t end_;
    int32_t step_;
public:
    Range(int32_t end) : current_(0), end_(end), step_(1) {}
    Range(int32_t start, int32_t end) : current_(start), end_(end), step_(1) {}
    Range(int32_t start, int32_t end, int32_t step) : current_(start), end_(end), step_(step) {
        if (step == 0) tpy_panic("range() arg 3 must not be zero");
    }

    std::optional<int32_t> next() {
        if (step_ > 0 ? current_ < end_ : current_ > end_) {
            int32_t val = current_;
            current_ = int32_add(current_, step_);
            return val;
        }
        return std::nullopt;
    }
};

} // namespace tpy
