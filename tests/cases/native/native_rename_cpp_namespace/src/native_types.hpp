#pragma once
#include <cstdint>

namespace mylib {
inline int32_t ns_add_impl(int32_t a, int32_t b) { return a + b; }
}

namespace other_ns {
inline int32_t other_add(int32_t a, int32_t b) { return a + b; }
}

inline int32_t global_mul(int32_t a, int32_t b) { return a * b; }
