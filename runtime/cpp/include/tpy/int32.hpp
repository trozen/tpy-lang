/**
 * TurboPython Runtime - Int32 Checked Arithmetic
 *
 * Overflow-checked operations for Int32 type with Python semantics.
 */

#pragma once

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
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

// --- Int32 conversion from other types ---

inline int32_t int32_from_float(double v) {
    if (std::isnan(v)) {
        tpy_panic("cannot convert float NaN to integer");
    }
    if (std::isinf(v)) {
        tpy_panic("cannot convert float infinity to integer");
    }
    if (v <= -2147483649.0 || v >= 2147483648.0) {
        tpy_panic("Int32 overflow: float value out of range");
    }
    return static_cast<int32_t>(v);
}

inline int32_t int32_from_str(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) ++start;
    size_t end = s.size();
    while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;

    if (start >= end) {
        std::string msg = "invalid literal for Int32() with base 10: '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    std::string trimmed(s.substr(start, end - start));
    char* endptr;
    errno = 0;
    long result = std::strtol(trimmed.c_str(), &endptr, 10);

    if (endptr != trimmed.c_str() + trimmed.size()) {
        std::string msg = "invalid literal for Int32() with base 10: '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    if (errno == ERANGE || result < INT32_MIN || result > INT32_MAX) {
        std::string msg = "Int32 overflow: value out of range for '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    return static_cast<int32_t>(result);
}

} // namespace tpy
