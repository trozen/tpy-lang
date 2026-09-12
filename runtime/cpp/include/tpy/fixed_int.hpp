/**
 * TurboPython Runtime - Fixed-Width Integer Checked Arithmetic
 *
 * Template-based overflow-checked operations for all fixed-width integer types.
 * Supports int8/16/32/64 and uint8/16/32/64 with Python semantics.
 *
 * The checked-arith helpers are marked constexpr so that `Final[IntN]`
 * initializers that reference other Finals can be constant-evaluated. The
 * overflow / divide-by-zero error branches route through non-constexpr
 * helpers (`raise_fixedint_overflow`, `raise<ZeroDivisionError>`,
 * `raise<ValueError>`); this is permitted under C++23 P2448 as long as those
 * branches are not reached during constant evaluation. When they are reached
 * (e.g. `Final[int32] = -INT32_MIN`), the compiler rejects the call, turning
 * a would-be runtime panic into a compile-time error.
 */

#pragma once

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>
#include "core.hpp"

namespace tpy {

// --- Checked power helper (reusable across all integer types) ---

template<typename T>
constexpr bool try_pow(T base, T exp, T& result) {
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

// --- Type name helper for error messages ---

template<typename T> constexpr const char* fixed_int_name() { return "FixedInt"; }
template<> constexpr const char* fixed_int_name<int8_t>() { return "int8"; }
template<> constexpr const char* fixed_int_name<int16_t>() { return "int16"; }
template<> constexpr const char* fixed_int_name<int32_t>() { return "int32"; }
template<> constexpr const char* fixed_int_name<int64_t>() { return "int64"; }
template<> constexpr const char* fixed_int_name<uint8_t>() { return "uint8"; }
template<> constexpr const char* fixed_int_name<uint16_t>() { return "uint16"; }
template<> constexpr const char* fixed_int_name<uint32_t>() { return "uint32"; }
template<> constexpr const char* fixed_int_name<uint64_t>() { return "uint64"; }

// --- Checked arithmetic ---

template<typename T>
constexpr T add_check(T a, T b) {
    T result;
    if (__builtin_add_overflow(a, b, &result)) {
        raise_fixedint_overflow("{} overflow in addition", fixed_int_name<T>());
    }
    return result;
}

template<typename T>
constexpr T sub_check(T a, T b) {
    T result;
    if (__builtin_sub_overflow(a, b, &result)) {
        raise_fixedint_overflow("{} overflow in subtraction", fixed_int_name<T>());
    }
    return result;
}

template<typename T>
constexpr T mul_check(T a, T b) {
    T result;
    if (__builtin_mul_overflow(a, b, &result)) {
        raise_fixedint_overflow("{} overflow in multiplication", fixed_int_name<T>());
    }
    return result;
}

// Python floor division: round toward negative infinity.
// Divisor must be non-zero (caller's responsibility).
template<typename T>
constexpr T div_floor(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (a == std::numeric_limits<T>::min() && b == static_cast<T>(-1)) [[unlikely]] {
            raise_fixedint_overflow("integer overflow in division");
        }
        T q = a / b;
        T r = a % b;
        if (r != 0 && ((r < 0) != (b < 0))) {
            q -= 1;
        }
        return q;
    } else {
        return a / b;
    }
}

// Python modulo: result has same sign as divisor.
// Divisor must be non-zero (caller's responsibility).
template<typename T>
constexpr T mod_floor(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (a == std::numeric_limits<T>::min() && b == static_cast<T>(-1)) [[unlikely]] {
            return 0;  // Python: INT_MIN % -1 == 0
        }
        T r = a % b;
        if (r != 0 && ((r < 0) != (b < 0))) {
            r += b;
        }
        return r;
    } else {
        return a % b;
    }
}

// Checked variants: also check for division by zero.
template<typename T>
constexpr T div_check(T a, T b) {
    if (b == 0) [[unlikely]] {
        raise_zero_division_error("integer division or modulo by zero");
    }
    return div_floor(a, b);
}

template<typename T>
constexpr T mod_check(T a, T b) {
    if (b == 0) [[unlikely]] {
        raise_zero_division_error("integer modulo by zero");
    }
    return mod_floor(a, b);
}

template<typename T>
constexpr T neg_check(T a) {
    static_assert(std::is_signed_v<T>, "Negation only supported on signed types");
    if (a == std::numeric_limits<T>::min()) {
        raise_fixedint_overflow("{} overflow in negation", fixed_int_name<T>());
    }
    return -a;
}

template<typename T>
constexpr T lshift_check(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (b < 0) {
            raise_value_error("negative shift count");
        }
    }
    constexpr int bits = sizeof(T) * 8;
    if (b >= static_cast<T>(bits)) {
        raise_fixedint_overflow("{} overflow in left shift", fixed_int_name<T>());
    }
    using U = std::make_unsigned_t<T>;
    T result = static_cast<T>(static_cast<U>(a) << static_cast<U>(b));
    if ((result >> b) != a) {
        raise_fixedint_overflow("{} overflow in left shift", fixed_int_name<T>());
    }
    return result;
}

template<typename T>
constexpr T rshift_check(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (b < 0) {
            raise_value_error("negative shift count");
        }
    }
    constexpr int bits = sizeof(T) * 8;
    if (b >= static_cast<T>(bits)) {
        raise_fixedint_overflow("{} shift count too large", fixed_int_name<T>());
    }
    return a >> b;
}

template<typename T>
constexpr T pow_check(T base, T exp) {
    if constexpr (std::is_signed_v<T>) {
        if (exp < 0) {
            // TPy-specific limitation: int**neg returns float in CPython, but
            // our static return type is T. Stays panic (not raise_fixedint_overflow)
            // because it's a language-shape issue, not a numeric overflow -- the
            // future fixed-int policy switch can never make this branch "none".
            tpy_panic("Negative exponent not supported (would require float)");
        }
    }
    T result;
    if (!try_pow(base, exp, result)) {
        raise_fixedint_overflow("{} overflow in power", fixed_int_name<T>());
    }
    return result;
}

// --- Checked cast between fixed-int types ---

template<typename To, typename From>
constexpr To int_cast_check(From v) {
    if constexpr (std::is_signed_v<From> && std::is_unsigned_v<To>) {
        if (v < 0 || static_cast<std::make_unsigned_t<From>>(v) > std::numeric_limits<To>::max()) {
            raise_fixedint_overflow("{} overflow: value out of range", fixed_int_name<To>());
        }
    } else if constexpr (std::is_unsigned_v<From> && std::is_signed_v<To>) {
        if (v > static_cast<std::make_unsigned_t<To>>(std::numeric_limits<To>::max())) {
            raise_fixedint_overflow("{} overflow: value out of range", fixed_int_name<To>());
        }
    } else if constexpr (sizeof(From) > sizeof(To)) {
        if (v < static_cast<From>(std::numeric_limits<To>::min()) ||
            v > static_cast<From>(std::numeric_limits<To>::max())) {
            raise_fixedint_overflow("{} overflow: value out of range", fixed_int_name<To>());
        }
    }
    return static_cast<To>(v);
}

// --- Conversion from other types ---

template<typename T>
T from_float_check(double v) {
    if (std::isnan(v)) {
        raise_value_error("cannot convert float NaN to integer");
    }
    if (std::isinf(v)) {
        raise_overflow_error("cannot convert float infinity to integer");
    }
    v = std::trunc(v);
    if constexpr (sizeof(T) <= 4) {
        // double represents all <=32-bit integer values exactly
        constexpr auto lo = static_cast<double>(std::numeric_limits<T>::min());
        constexpr auto hi = static_cast<double>(std::numeric_limits<T>::max());
        if (v < lo || v > hi) {
            raise_fixedint_overflow("{} overflow: float value out of range",
                                    fixed_int_name<T>());
        }
    } else if constexpr (std::is_signed_v<T>) {
        // int64_t range [-2^63, 2^63-1]: use exact power-of-2 bounds
        // to avoid static_cast<double>(max) rounding up past the true max
        constexpr double lo = -9223372036854775808.0;  // -2^63, exact
        constexpr double hi = 9223372036854775808.0;   //  2^63, exact
        if (v < lo || v >= hi) {
            raise_fixedint_overflow("{} overflow: float value out of range",
                                    fixed_int_name<T>());
        }
    } else {
        // uint64_t range [0, 2^64-1]: 2^64 is exact in double
        constexpr double hi = 18446744073709551616.0;  // 2^64, exact
        if (v < 0.0 || v >= hi) {
            raise_fixedint_overflow("{} overflow: float value out of range",
                                    fixed_int_name<T>());
        }
    }
    return static_cast<T>(v);
}

template<typename T>
T from_str_check(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) ++start;
    size_t end = s.size();
    while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;

    auto type_name = fixed_int_name<T>();

    if (start >= end) {
        raise_value_error("invalid literal for {}() with base 10: '{}'", type_name, s);
    }

    std::string trimmed(s.substr(start, end - start));
    char* endptr;
    errno = 0;

    if constexpr (std::is_signed_v<T>) {
        long long result = std::strtoll(trimmed.c_str(), &endptr, 10);
        if (endptr != trimmed.c_str() + trimmed.size()) {
            raise_value_error("invalid literal for {}() with base 10: '{}'", type_name, s);
        }
        if (errno == ERANGE || result < static_cast<long long>(std::numeric_limits<T>::min())
                            || result > static_cast<long long>(std::numeric_limits<T>::max())) {
            raise_fixedint_overflow("{} overflow: value out of range for '{}'", type_name, s);
        }
        return static_cast<T>(result);
    } else {
        // Check for negative sign on unsigned types
        if (trimmed[0] == '-') {
            raise_fixedint_overflow("{} overflow: negative value for unsigned type '{}'",
                                    type_name, s);
        }
        unsigned long long result = std::strtoull(trimmed.c_str(), &endptr, 10);
        if (endptr != trimmed.c_str() + trimmed.size()) {
            raise_value_error("invalid literal for {}() with base 10: '{}'", type_name, s);
        }
        if (errno == ERANGE || result > static_cast<unsigned long long>(std::numeric_limits<T>::max())) {
            raise_fixedint_overflow("{} overflow: value out of range for '{}'", type_name, s);
        }
        return static_cast<T>(result);
    }
}

} // namespace tpy
