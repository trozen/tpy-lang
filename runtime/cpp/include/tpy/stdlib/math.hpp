#pragma once

#include <cmath>
#include <cstdint>
#include <limits>
#include <tuple>

namespace tpy::stdlib::math {

inline std::tuple<double, double> modf(double x) {
    double int_part;
    double frac = std::modf(x, &int_part);
    return {frac, int_part};
}

// Exponent template parameter. For IEEE 754 double, std::frexp normalizes
// |m| to [0.5, 1), so exp is in [-1073, 1024] (the lower bound comes from
// subnormals: smallest positive denormal is 2^-1074 -> m=0.5, exp=-1073;
// upper bound from max finite double just under 2^1024). Safely fits in
// any 16+ bit signed int. Caller picks the int type (defaults to
// DefaultInt on the Python side); `T(exp)` uses the target's constructor
// so BigInt also works.
template<typename T>
inline std::tuple<double, T> frexp(double x) {
    int exp;
    double m = std::frexp(x, &exp);
    return {m, T(exp)};
}

// Unit in the Last Place: distance to the next representable double > |x|.
// Matches CPython math.ulp: ulp(nan)=nan, ulp(+-inf)=inf, ulp(0)=smallest
// subnormal, else nextafter(|x|, inf) - |x|.
inline double ulp(double x) {
    if (std::isnan(x)) return x;
    if (std::isinf(x)) return std::numeric_limits<double>::infinity();
    if (x == 0.0) return std::numeric_limits<double>::denorm_min();
    double ax = std::fabs(x);
    return std::nextafter(ax, std::numeric_limits<double>::infinity()) - ax;
}

}  // namespace tpy::stdlib::math
