// tpy::Slice -- built-in slice type for user-defined __getitem__ overloads.
// Attributes are Optional[Int32], matching CPython's slice(start, stop).
#pragma once

#include <cstdint>
#include <optional>

namespace tpy {

struct Slice {
    std::optional<int32_t> start;
    std::optional<int32_t> stop;
};

} // namespace tpy
