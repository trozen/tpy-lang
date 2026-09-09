// tpy::BasicSlice / tpy::Slice -- built-in slice types for subscript ranges.
// BasicSlice has start/stop only (for a[1:3] syntax).
// Slice adds step (for a[1:3:2] syntax).
#pragma once

#include "type_traits.hpp"

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

// Both are plain bundles of optional ints -- value types, and Send / Sync by
// the default that follows.
template<> struct is_value_type<BasicSlice> : std::true_type {};
template<> struct is_value_type<Slice> : std::true_type {};

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
