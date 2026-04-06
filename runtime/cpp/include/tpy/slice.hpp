// tpy::BasicSlice / tpy::Slice -- built-in slice types for subscript ranges.
// BasicSlice has start/stop only (for a[1:3] syntax).
// Slice adds step (for a[1:3:2] syntax).
#pragma once

#include <cstdint>
#include <optional>

namespace tpy {

struct BasicSlice {
    std::optional<int32_t> start;
    std::optional<int32_t> stop;
};

struct Slice {
    std::optional<int32_t> start;
    std::optional<int32_t> stop;
    std::optional<int32_t> step;

    Slice() = default;
    Slice(std::optional<int32_t> s, std::optional<int32_t> e, std::optional<int32_t> st)
        : start(s), stop(e), step(st) {}
    // Implicit conversion from BasicSlice (basic_slice coerces to slice)
    Slice(const BasicSlice& bs) : start(bs.start), stop(bs.stop), step(std::nullopt) {}
};

} // namespace tpy
