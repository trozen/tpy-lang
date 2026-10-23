#pragma once
#include <cstdint>
#include <optional>
namespace nb {
inline int32_t opt(int32_t x, std::optional<int32_t> y = 9) { return x + y.value_or(0); }
}
