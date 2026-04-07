// tpy::BasicSlice / tpy::Slice -- built-in slice types for subscript ranges.
// BasicSlice has start/stop only (for a[1:3] syntax).
// Slice adds step (for a[1:3:2] syntax).
#pragma once

#include <cstdint>
#include <optional>
#include <ostream>

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

inline std::ostream& operator<<(std::ostream& os, const BasicSlice& s) {
    os << "basic_slice(";
    if (s.start) os << *s.start; else os << "None";
    os << ", ";
    if (s.stop) os << *s.stop; else os << "None";
    return os << ")";
}

inline std::ostream& operator<<(std::ostream& os, const Slice& s) {
    os << "slice(";
    if (s.start) os << *s.start; else os << "None";
    os << ", ";
    if (s.stop) os << *s.stop; else os << "None";
    os << ", ";
    if (s.step) os << *s.step; else os << "None";
    return os << ")";
}

} // namespace tpy
