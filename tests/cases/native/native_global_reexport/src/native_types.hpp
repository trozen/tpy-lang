#pragma once
#include <cstdint>

// Backing symbol for leaf.py's `native_global("tpy_test_global", binding="C")`.
extern "C" std::int32_t tpy_test_global;
