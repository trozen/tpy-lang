#include "native_types.hpp"

extern "C" int32_t native_abs(int32_t x) {
    return x < 0 ? -x : x;
}

extern "C" int32_t native_add(int32_t a, int32_t b) {
    return a + b;
}
