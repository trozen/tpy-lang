#include <cstdint>

extern "C" int32_t abs(int32_t x) {
    return x < 0 ? -x : x;
}

extern "C" int32_t tpy_clock(void) {
    return 42;
}
