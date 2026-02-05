/**
 * TurboPython Runtime - Int32 Checked Arithmetic
 *
 * Overflow-checked operations for Int32 type with Python semantics.
 */

#pragma once

#include <cstdint>
#include "core.hpp"

namespace tpy {

// --- Checked power (reusable for BigInt fast path and Int32) ---

// Computes base^exp with overflow detection. Returns true on success, false on overflow.
template<typename T>
bool checked_pow(T base, T exp, T& result) {
    if (exp < 0) return false;
    if (exp == 0) { result = 1; return true; }

    result = 1;
    T b = base;

    while (exp > 0) {
        if (exp & 1) {
            if (__builtin_mul_overflow(result, b, &result)) {
                return false;
            }
        }
        exp >>= 1;
        if (exp > 0) {
            if (__builtin_mul_overflow(b, b, &b)) {
                return false;
            }
        }
    }
    return true;
}

// --- Int32 checked arithmetic ---

inline int32_t int32_add(int32_t a, int32_t b) {
    int32_t result;
    if (__builtin_add_overflow(a, b, &result)) {
        tpy_panic("Int32 overflow in addition");
    }
    return result;
}

inline int32_t int32_sub(int32_t a, int32_t b) {
    int32_t result;
    if (__builtin_sub_overflow(a, b, &result)) {
        tpy_panic("Int32 overflow in subtraction");
    }
    return result;
}

inline int32_t int32_mul(int32_t a, int32_t b) {
    int32_t result;
    if (__builtin_mul_overflow(a, b, &result)) {
        tpy_panic("Int32 overflow in multiplication");
    }
    return result;
}

inline int32_t int32_div(int32_t a, int32_t b) {
    if (b == 0) {
        tpy_panic("Division by zero");
    }
    if (a == INT32_MIN && b == -1) {
        tpy_panic("Int32 overflow in division");
    }
    // Python floor division: round toward negative infinity
    int32_t q = a / b;
    int32_t r = a % b;
    // Adjust if remainder has opposite sign to divisor
    if (r != 0 && ((r < 0) != (b < 0))) {
        q -= 1;
    }
    return q;
}

inline int32_t int32_mod(int32_t a, int32_t b) {
    if (b == 0) {
        tpy_panic("Division by zero");
    }
    // Python modulo: result has same sign as divisor
    int32_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        r += b;
    }
    return r;
}

inline int32_t int32_neg(int32_t a) {
    if (a == INT32_MIN) {
        tpy_panic("Int32 overflow in negation");
    }
    return -a;
}

inline int32_t int32_lshift(int32_t a, int32_t b) {
    if (b < 0) {
        tpy_panic("Negative shift count");
    }
    if (b >= 32) {
        tpy_panic("Int32 overflow in left shift");
    }
    // Check for overflow: shifting would lose significant bits
    int32_t result = a << b;
    if ((result >> b) != a) {
        tpy_panic("Int32 overflow in left shift");
    }
    return result;
}

inline int32_t int32_rshift(int32_t a, int32_t b) {
    if (b < 0) {
        tpy_panic("Negative shift count");
    }
    if (b >= 32) {
        tpy_panic("Int32 shift count too large");
    }
    return a >> b;  // Arithmetic right shift for signed integers
}

inline int32_t int32_pow(int32_t base, int32_t exp) {
    if (exp < 0) {
        tpy_panic("Negative exponent not supported (would require float)");
    }
    int32_t result;
    if (!checked_pow(base, exp, result)) {
        tpy_panic("Int32 overflow in power");
    }
    return result;
}

} // namespace tpy
