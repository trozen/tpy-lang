#pragma once
#include <cstdint>

struct ScaleNative {
    int32_t mul_i(int64_t x) const { return 11; }
    int32_t mul_f(double x) const { return 12; }
};

inline bool scale_less(const ScaleNative&, const tpy::BigInt& x) { return x > 2; }
