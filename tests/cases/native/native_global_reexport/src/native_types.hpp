#pragma once
#include <cstdint>
#include <vector>

// Backing symbol for leaf.py's `native_global("tpy_test_global", binding="C")`;
// const, matching the `extern const` a `Final` native_global declares.
extern "C" const std::int32_t tpy_test_global;

// Backing symbol for leaf.py's non-value `native_global("tpy_test_xs")`.
extern std::vector<std::int32_t> tpy_test_xs;
