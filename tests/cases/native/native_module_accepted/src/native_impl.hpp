#pragma once
#include <cstdint>

namespace nativelib {
inline int32_t native_func(int32_t x) { return x + 1; }
struct NativeClass { int32_t value; };
}

extern "C" int32_t native_c_func(int32_t x);
