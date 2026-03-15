/**
 * TurboPython Runtime - Math Operations
 *
 * Wrapper for math.log(x, base) two-argument form.
 * Single-argument functions map directly to std:: via @native in
 * lib/stdlib/math.py. floor/ceil use BigInt::from_floor/from_ceil.
 */

#pragma once

#include <cmath>

namespace tpy::math {

inline double log_base(double x, double base) {
    return std::log(x) / std::log(base);
}

} // namespace tpy::math
