/**
 * TurboPython Runtime - Fixed-Width Integer Checked Arithmetic
 *
 * Template-based overflow-checked operations for all fixed-width integer types.
 * Supports Int8/16/32/64 and UInt8/16/32/64 with Python semantics.
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
#include "int32.hpp"

namespace tpy {

// --- Type name helper for error messages ---

template<typename T> constexpr const char* fixed_int_name() { return "FixedInt"; }
template<> constexpr const char* fixed_int_name<int8_t>() { return "Int8"; }
template<> constexpr const char* fixed_int_name<int16_t>() { return "Int16"; }
template<> constexpr const char* fixed_int_name<int32_t>() { return "Int32"; }
template<> constexpr const char* fixed_int_name<int64_t>() { return "Int64"; }
template<> constexpr const char* fixed_int_name<uint8_t>() { return "UInt8"; }
template<> constexpr const char* fixed_int_name<uint16_t>() { return "UInt16"; }
template<> constexpr const char* fixed_int_name<uint32_t>() { return "UInt32"; }
template<> constexpr const char* fixed_int_name<uint64_t>() { return "UInt64"; }

// --- Checked arithmetic ---

template<typename T>
T add_check(T a, T b) {
    T result;
    if (__builtin_add_overflow(a, b, &result)) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in addition";
        tpy_panic(msg.c_str());
    }
    return result;
}

template<typename T>
T sub_check(T a, T b) {
    T result;
    if (__builtin_sub_overflow(a, b, &result)) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in subtraction";
        tpy_panic(msg.c_str());
    }
    return result;
}

template<typename T>
T mul_check(T a, T b) {
    T result;
    if (__builtin_mul_overflow(a, b, &result)) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in multiplication";
        tpy_panic(msg.c_str());
    }
    return result;
}

template<typename T>
T div_check(T a, T b) {
    if (b == 0) {
        tpy_panic("Division by zero");
    }
    if constexpr (std::is_signed_v<T>) {
        if (a == std::numeric_limits<T>::min() && b == static_cast<T>(-1)) {
            std::string msg = std::string(fixed_int_name<T>()) + " overflow in division";
            tpy_panic(msg.c_str());
        }
        // Python floor division: round toward negative infinity
        T q = a / b;
        T r = a % b;
        if (r != 0 && ((r < 0) != (b < 0))) {
            q -= 1;
        }
        return q;
    } else {
        // Unsigned: truncation division (same as floor for non-negative)
        return a / b;
    }
}

template<typename T>
T mod_check(T a, T b) {
    if (b == 0) {
        tpy_panic("Division by zero");
    }
    if constexpr (std::is_signed_v<T>) {
        // Python modulo: result has same sign as divisor
        T r = a % b;
        if (r != 0 && ((r < 0) != (b < 0))) {
            r += b;
        }
        return r;
    } else {
        return a % b;
    }
}

template<typename T>
T neg_check(T a) {
    static_assert(std::is_signed_v<T>, "Negation only supported on signed types");
    if (a == std::numeric_limits<T>::min()) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in negation";
        tpy_panic(msg.c_str());
    }
    return -a;
}

template<typename T>
T lshift_check(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (b < 0) {
            tpy_panic("Negative shift count");
        }
    }
    constexpr int bits = sizeof(T) * 8;
    if (b >= static_cast<T>(bits)) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in left shift";
        tpy_panic(msg.c_str());
    }
    using U = std::make_unsigned_t<T>;
    T result = static_cast<T>(static_cast<U>(a) << static_cast<U>(b));
    if ((result >> b) != a) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in left shift";
        tpy_panic(msg.c_str());
    }
    return result;
}

template<typename T>
T rshift_check(T a, T b) {
    if constexpr (std::is_signed_v<T>) {
        if (b < 0) {
            tpy_panic("Negative shift count");
        }
    }
    constexpr int bits = sizeof(T) * 8;
    if (b >= static_cast<T>(bits)) {
        std::string msg = std::string(fixed_int_name<T>()) + " shift count too large";
        tpy_panic(msg.c_str());
    }
    return a >> b;
}

template<typename T>
T pow_check(T base, T exp) {
    if constexpr (std::is_signed_v<T>) {
        if (exp < 0) {
            tpy_panic("Negative exponent not supported (would require float)");
        }
    }
    T result;
    if (!try_pow(base, exp, result)) {
        std::string msg = std::string(fixed_int_name<T>()) + " overflow in power";
        tpy_panic(msg.c_str());
    }
    return result;
}

// --- Checked cast between fixed-int types ---

template<typename To, typename From>
To int_cast_check(From v) {
    if constexpr (std::is_signed_v<From> && std::is_unsigned_v<To>) {
        if (v < 0 || static_cast<std::make_unsigned_t<From>>(v) > std::numeric_limits<To>::max()) {
            std::string msg = std::string(fixed_int_name<To>()) + " overflow: value out of range";
            tpy_panic(msg.c_str());
        }
    } else if constexpr (std::is_unsigned_v<From> && std::is_signed_v<To>) {
        if (v > static_cast<std::make_unsigned_t<To>>(std::numeric_limits<To>::max())) {
            std::string msg = std::string(fixed_int_name<To>()) + " overflow: value out of range";
            tpy_panic(msg.c_str());
        }
    } else if constexpr (sizeof(From) > sizeof(To)) {
        if (v < static_cast<From>(std::numeric_limits<To>::min()) ||
            v > static_cast<From>(std::numeric_limits<To>::max())) {
            std::string msg = std::string(fixed_int_name<To>()) + " overflow: value out of range";
            tpy_panic(msg.c_str());
        }
    }
    return static_cast<To>(v);
}

// --- Conversion from other types ---

template<typename T>
T from_float_check(double v) {
    if (std::isnan(v)) {
        tpy_panic("cannot convert float NaN to integer");
    }
    if (std::isinf(v)) {
        tpy_panic("cannot convert float infinity to integer");
    }
    v = std::trunc(v);
    if constexpr (sizeof(T) <= 4) {
        // double represents all <=32-bit integer values exactly
        constexpr auto lo = static_cast<double>(std::numeric_limits<T>::min());
        constexpr auto hi = static_cast<double>(std::numeric_limits<T>::max());
        if (v < lo || v > hi) {
            std::string msg = std::string(fixed_int_name<T>()) + " overflow: float value out of range";
            tpy_panic(msg.c_str());
        }
    } else if constexpr (std::is_signed_v<T>) {
        // int64_t range [-2^63, 2^63-1]: use exact power-of-2 bounds
        // to avoid static_cast<double>(max) rounding up past the true max
        constexpr double lo = -9223372036854775808.0;  // -2^63, exact
        constexpr double hi = 9223372036854775808.0;   //  2^63, exact
        if (v < lo || v >= hi) {
            std::string msg = std::string(fixed_int_name<T>()) + " overflow: float value out of range";
            tpy_panic(msg.c_str());
        }
    } else {
        // uint64_t range [0, 2^64-1]: 2^64 is exact in double
        constexpr double hi = 18446744073709551616.0;  // 2^64, exact
        if (v < 0.0 || v >= hi) {
            std::string msg = std::string(fixed_int_name<T>()) + " overflow: float value out of range";
            tpy_panic(msg.c_str());
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

    std::string type_name = fixed_int_name<T>();

    if (start >= end) {
        std::string msg = "invalid literal for " + type_name + "() with base 10: '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    std::string trimmed(s.substr(start, end - start));
    char* endptr;
    errno = 0;

    if constexpr (std::is_signed_v<T>) {
        long long result = std::strtoll(trimmed.c_str(), &endptr, 10);
        if (endptr != trimmed.c_str() + trimmed.size()) {
            std::string msg = "invalid literal for " + type_name + "() with base 10: '" + std::string(s) + "'";
            tpy_panic(msg.c_str());
        }
        if (errno == ERANGE || result < static_cast<long long>(std::numeric_limits<T>::min())
                            || result > static_cast<long long>(std::numeric_limits<T>::max())) {
            std::string msg = type_name + " overflow: value out of range for '" + std::string(s) + "'";
            tpy_panic(msg.c_str());
        }
        return static_cast<T>(result);
    } else {
        // Check for negative sign on unsigned types
        if (trimmed[0] == '-') {
            std::string msg = type_name + " overflow: negative value for unsigned type '" + std::string(s) + "'";
            tpy_panic(msg.c_str());
        }
        unsigned long long result = std::strtoull(trimmed.c_str(), &endptr, 10);
        if (endptr != trimmed.c_str() + trimmed.size()) {
            std::string msg = "invalid literal for " + type_name + "() with base 10: '" + std::string(s) + "'";
            tpy_panic(msg.c_str());
        }
        if (errno == ERANGE || result > static_cast<unsigned long long>(std::numeric_limits<T>::max())) {
            std::string msg = type_name + " overflow: value out of range for '" + std::string(s) + "'";
            tpy_panic(msg.c_str());
        }
        return static_cast<T>(result);
    }
}

} // namespace tpy
