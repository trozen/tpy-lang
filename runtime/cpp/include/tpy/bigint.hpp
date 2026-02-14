/**
 * TurboPython Runtime - BigInt
 *
 * Arbitrary precision integer with small-int optimization.
 * Uses pointer tagging to store small integers inline.
 */

#pragma once

#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>
#include <gmp.h>

#include "core.hpp"
#include "int32.hpp"
#include "fixed_int.hpp"
#include "type_traits.hpp"

namespace tpy {

/**
 * BigInt - Arbitrary precision integer with small-int optimization.
 *
 * Uses pointer tagging to store small integers inline:
 * - Tag in lo_ & 1: 0 = small, 1 = big (GMP mpz_t*)
 * - Small: value stored as lo_ >> 1 (63-bit signed range)
 * - Big: hi_ is pointer to mpz_t
 *
 * Python semantics for division and modulo (floor division).
 */
class BigInt {
public:
    BigInt() noexcept : lo_(0), hi_(0) {}

    BigInt(int32_t v) noexcept : lo_(static_cast<int64_t>(v) << 1), hi_(0) {}

    BigInt(int64_t v) noexcept {
        if (fits_small(v)) {
            lo_ = v << 1;
            hi_ = 0;
        } else {
            init_gmp(v);
        }
    }

    BigInt(uint64_t v) noexcept {
        if (v <= static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) && fits_small(static_cast<int64_t>(v))) {
            lo_ = static_cast<int64_t>(v) << 1;
            hi_ = 0;
        } else {
            init_gmp_ui(v);
        }
    }

    BigInt(const BigInt& other) noexcept {
        if (other.is_small()) {
            lo_ = other.lo_;
            hi_ = 0;
        } else {
            copy_gmp(other);
        }
    }

    BigInt(BigInt&& other) noexcept : lo_(other.lo_), hi_(other.hi_) {
        other.lo_ = 0;
        other.hi_ = 0;
    }

    ~BigInt() {
        free_gmp();
    }

    BigInt& operator=(const BigInt& other) noexcept {
        if (this != &other) {
            free_gmp();
            if (other.is_small()) {
                lo_ = other.lo_;
                hi_ = 0;
            } else {
                copy_gmp(other);
            }
        }
        return *this;
    }

    BigInt& operator=(BigInt&& other) noexcept {
        if (this != &other) {
            free_gmp();
            lo_ = other.lo_;
            hi_ = other.hi_;
            other.lo_ = 0;
            other.hi_ = 0;
        }
        return *this;
    }

    bool is_small() const noexcept { return (lo_ & 1) == 0; }

    int64_t small_value() const noexcept { return lo_ >> 1; }

    // Arithmetic operators
    BigInt operator+(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_add_overflow(a, b, &result) && fits_small(result)) {
                BigInt r;
                r.lo_ = result << 1;
                return r;
            }
        }
        return add_gmp(*this, rhs);
    }

    BigInt operator-(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_sub_overflow(a, b, &result) && fits_small(result)) {
                BigInt r;
                r.lo_ = result << 1;
                return r;
            }
        }
        return sub_gmp(*this, rhs);
    }

    BigInt operator*(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_mul_overflow(a, b, &result) && fits_small(result)) {
                BigInt r;
                r.lo_ = result << 1;
                return r;
            }
        }
        return mul_gmp(*this, rhs);
    }

    BigInt operator/(const BigInt& rhs) const {
        return floor_div(rhs);
    }

    BigInt operator%(const BigInt& rhs) const {
        return floor_mod(rhs);
    }

    BigInt operator-() const {
        if (is_small()) {
            int64_t v = small_value();
            if (v != INT64_MIN && fits_small(-v)) {
                BigInt r;
                r.lo_ = (-v) << 1;
                return r;
            }
        }
        return neg_gmp(*this);
    }

    // Compound assignment operators
    BigInt& operator+=(const BigInt& rhs) { *this = *this + rhs; return *this; }
    BigInt& operator-=(const BigInt& rhs) { *this = *this - rhs; return *this; }
    BigInt& operator*=(const BigInt& rhs) { *this = *this * rhs; return *this; }
    BigInt& operator/=(const BigInt& rhs) { *this = *this / rhs; return *this; }
    BigInt& operator%=(const BigInt& rhs) { *this = *this % rhs; return *this; }

    // Increment/decrement operators
    BigInt& operator++() { *this += BigInt(1); return *this; }
    BigInt operator++(int) { BigInt tmp = *this; ++(*this); return tmp; }
    BigInt& operator--() { *this -= BigInt(1); return *this; }
    BigInt operator--(int) { BigInt tmp = *this; --(*this); return tmp; }

    // Shift operators (Python semantics: arbitrary precision)
    BigInt operator<<(int32_t shift) const {
        if (shift < 0) {
            tpy_panic("Negative shift count");
        }
        if (shift == 0) return *this;
        return lshift_gmp(*this, shift);
    }

    BigInt operator>>(int32_t shift) const {
        if (shift < 0) {
            tpy_panic("Negative shift count");
        }
        if (shift == 0) return *this;
        return rshift_gmp(*this, shift);
    }

    BigInt& operator<<=(int32_t shift) { *this = *this << shift; return *this; }
    BigInt& operator>>=(int32_t shift) { *this = *this >> shift; return *this; }

    // BigInt overloads for shift (convert to int32)
    BigInt operator<<(const BigInt& shift) const { return *this << shift.to_int32_check(); }
    BigInt operator>>(const BigInt& shift) const { return *this >> shift.to_int32_check(); }

    // Bitwise operators
    BigInt operator&(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            // (a << 1) & (b << 1) = (a & b) << 1, LSB stays 0
            BigInt r;
            r.lo_ = lo_ & rhs.lo_;
            return r;
        }
        return and_gmp(*this, rhs);
    }

    BigInt operator|(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            // (a << 1) | (b << 1) = (a | b) << 1, LSB stays 0
            BigInt r;
            r.lo_ = lo_ | rhs.lo_;
            return r;
        }
        return or_gmp(*this, rhs);
    }

    BigInt operator^(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            // (a << 1) ^ (b << 1) = (a ^ b) << 1, LSB stays 0
            BigInt r;
            r.lo_ = lo_ ^ rhs.lo_;
            return r;
        }
        return xor_gmp(*this, rhs);
    }

    BigInt operator~() const {
        // Python: ~x = -(x+1)
        if (is_small()) {
            int64_t v = small_value();
            int64_t result = -(v + 1);
            if (fits_small(result)) {
                BigInt r;
                r.lo_ = result << 1;
                return r;
            }
        }
        return invert_gmp(*this);
    }

    BigInt& operator&=(const BigInt& rhs) { *this = *this & rhs; return *this; }
    BigInt& operator|=(const BigInt& rhs) { *this = *this | rhs; return *this; }
    BigInt& operator^=(const BigInt& rhs) { *this = *this ^ rhs; return *this; }

    // Power operator (Python semantics: negative exponent not supported)
    BigInt pow(const BigInt& exp) const;

    // Comparison operators
    bool operator==(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            return lo_ == rhs.lo_;
        }
        return compare(rhs) == 0;
    }

    bool operator!=(const BigInt& rhs) const { return !(*this == rhs); }

    bool operator<(const BigInt& rhs) const { return compare(rhs) < 0; }
    bool operator<=(const BigInt& rhs) const { return compare(rhs) <= 0; }
    bool operator>(const BigInt& rhs) const { return compare(rhs) > 0; }
    bool operator>=(const BigInt& rhs) const { return compare(rhs) >= 0; }

    // Conversion to int32_t (for len() interop, etc.)
    int32_t to_int32_check() const {
        if (is_small()) {
            int64_t v = small_value();
            if (v >= INT32_MIN && v <= INT32_MAX) {
                return static_cast<int32_t>(v);
            }
        }
        tpy_panic("Int32 overflow: value out of range");
    }

    // Generic conversion to any fixed-width integer type
    template<typename T>
    T to_fixed_check() const {
        if (is_small()) {
            int64_t v = small_value();
            if constexpr (std::is_signed_v<T>) {
                if (v >= static_cast<int64_t>(std::numeric_limits<T>::min()) &&
                    v <= static_cast<int64_t>(std::numeric_limits<T>::max())) {
                    return static_cast<T>(v);
                }
            } else {
                if (v >= 0 && static_cast<uint64_t>(v) <= static_cast<uint64_t>(std::numeric_limits<T>::max())) {
                    return static_cast<T>(v);
                }
            }
        } else {
            // GMP path for large values — only int64_t/uint64_t might fit
            if constexpr (std::is_same_v<T, int64_t>) {
                if (mpz_fits_slong_p(gmp_ptr())) {
                    return static_cast<int64_t>(mpz_get_si(gmp_ptr()));
                }
            } else if constexpr (std::is_same_v<T, uint64_t>) {
                if (mpz_sgn(gmp_ptr()) >= 0 && mpz_fits_ulong_p(gmp_ptr())) {
                    return static_cast<uint64_t>(mpz_get_ui(gmp_ptr()));
                }
            }
        }
        std::string msg = std::string(tpy::fixed_int_name<T>()) + " overflow: value out of range";
        tpy_panic(msg.c_str());
    }

    // Truncating conversion to any fixed-width integer type (modular reduction, never panics)
    template<typename T>
    T to_fixed_trunc() const {
        constexpr int bits = sizeof(T) * 8;
        if (is_small()) {
            // Small path: just truncate the int64_t value
            return static_cast<T>(small_value());
        }
        // GMP path: extract low N bits via mpz_get_ui / mpz_tdiv_r_2exp
        mpz_t tmp;
        mpz_init(tmp);
        mpz_tdiv_r_2exp(tmp, gmp_ptr(), bits);
        // mpz_tdiv_r_2exp result is always non-negative
        uint64_t low_bits = mpz_get_ui(tmp);
        mpz_clear(tmp);
        return static_cast<T>(low_bits);
    }

    // Conversion to double (for float operations)
    double to_double() const {
        if (is_small()) {
            return static_cast<double>(small_value());
        }
        return mpz_get_d(gmp_ptr());
    }

    // Explicit conversion operator for static_cast<double>
    explicit operator double() const {
        return to_double();
    }

    // String conversion for printing
    std::string to_string() const {
        if (is_small()) {
            return std::to_string(small_value());
        }
        char* str = mpz_get_str(nullptr, 10, gmp_ptr());
        std::string result(str);
        std::free(str);
        return result;
    }

    // Absolute value (static method)
    static BigInt abs(const BigInt& x) {
        return x < BigInt(0) ? -x : x;
    }

    // Check if value is zero (useful for conditionals)
    explicit operator bool() const {
        if (is_small()) {
            return small_value() != 0;
        }
        return mpz_sgn(gmp_ptr()) != 0;
    }

    // Static factory methods for conversions
    static BigInt from_float(double v);
    static BigInt from_str(std::string_view s);

private:
    int64_t lo_;  // bit 0: tag. small: value << 1. big: 1
    int64_t hi_;  // small: unused. big: pointer to mpz_t

    static constexpr int64_t SMALL_MAX = (INT64_MAX >> 1);
    static constexpr int64_t SMALL_MIN = (INT64_MIN >> 1);

    static bool fits_small(int64_t v) noexcept {
        return v >= SMALL_MIN && v <= SMALL_MAX;
    }

    mpz_ptr gmp_ptr() const noexcept {
        return reinterpret_cast<mpz_ptr>(hi_);
    }

    void init_gmp(int64_t v) {
        auto* z = new __mpz_struct;
        mpz_init_set_si(z, v);
        lo_ = 1;
        hi_ = reinterpret_cast<int64_t>(z);
    }

    void init_gmp_ui(uint64_t v) {
        auto* z = new __mpz_struct;
        mpz_init_set_ui(z, v);
        lo_ = 1;
        hi_ = reinterpret_cast<int64_t>(z);
    }

    void free_gmp() noexcept {
        if (!is_small() && hi_ != 0) {
            mpz_clear(gmp_ptr());
            delete gmp_ptr();
        }
    }

    void copy_gmp(const BigInt& other) {
        auto* z = new __mpz_struct;
        mpz_init_set(z, other.gmp_ptr());
        lo_ = 1;
        hi_ = reinterpret_cast<int64_t>(z);
    }

    void to_mpz(mpz_t z) const {
        if (is_small()) {
            mpz_init_set_si(z, small_value());
        } else {
            mpz_init_set(z, gmp_ptr());
        }
    }

    static BigInt from_mpz(mpz_t z) {
        BigInt result;
        if (mpz_fits_slong_p(z)) {
            long v = mpz_get_si(z);
            if (fits_small(v)) {
                result.lo_ = static_cast<int64_t>(v) << 1;
                result.hi_ = 0;
                mpz_clear(z);
                return result;
            }
        }
        auto* zp = new __mpz_struct;
        mpz_init_set(zp, z);
        mpz_clear(z);
        result.lo_ = 1;
        result.hi_ = reinterpret_cast<int64_t>(zp);
        return result;
    }

    int compare(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            return (a > b) - (a < b);
        }

        mpz_t a, b;
        to_mpz(a);
        rhs.to_mpz(b);
        int result = mpz_cmp(a, b);
        mpz_clear(a);
        mpz_clear(b);
        return result;
    }

    // GMP-based arithmetic helpers
    static BigInt add_gmp(const BigInt& a, const BigInt& b);
    static BigInt sub_gmp(const BigInt& a, const BigInt& b);
    static BigInt mul_gmp(const BigInt& a, const BigInt& b);
    static BigInt neg_gmp(const BigInt& a);
    static BigInt lshift_gmp(const BigInt& a, int32_t shift);
    static BigInt rshift_gmp(const BigInt& a, int32_t shift);
    static BigInt and_gmp(const BigInt& a, const BigInt& b);
    static BigInt or_gmp(const BigInt& a, const BigInt& b);
    static BigInt xor_gmp(const BigInt& a, const BigInt& b);
    static BigInt invert_gmp(const BigInt& a);

    BigInt floor_div(const BigInt& rhs) const;
    BigInt floor_mod(const BigInt& rhs) const;
};

// Implementation of GMP-based big integer operations

inline BigInt BigInt::add_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_add(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::sub_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_sub(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::mul_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_mul(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::neg_gmp(const BigInt& a) {
    mpz_t za, result;
    a.to_mpz(za);
    mpz_init(result);
    mpz_neg(result, za);
    mpz_clear(za);
    return from_mpz(result);
}

inline BigInt BigInt::lshift_gmp(const BigInt& a, int32_t shift) {
    mpz_t za, result;
    a.to_mpz(za);
    mpz_init(result);
    mpz_mul_2exp(result, za, static_cast<mp_bitcnt_t>(shift));
    mpz_clear(za);
    return from_mpz(result);
}

inline BigInt BigInt::rshift_gmp(const BigInt& a, int32_t shift) {
    mpz_t za, result;
    a.to_mpz(za);
    mpz_init(result);
    mpz_fdiv_q_2exp(result, za, static_cast<mp_bitcnt_t>(shift));  // Floor division by 2^shift
    mpz_clear(za);
    return from_mpz(result);
}

inline BigInt BigInt::and_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_and(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::or_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_ior(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::xor_gmp(const BigInt& a, const BigInt& b) {
    mpz_t za, zb, result;
    a.to_mpz(za);
    b.to_mpz(zb);
    mpz_init(result);
    mpz_xor(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::invert_gmp(const BigInt& a) {
    mpz_t za, result;
    a.to_mpz(za);
    mpz_init(result);
    mpz_com(result, za);  // Python: ~x = -(x+1) = one's complement
    mpz_clear(za);
    return from_mpz(result);
}

inline BigInt BigInt::floor_div(const BigInt& rhs) const {
    // Check for division by zero
    bool rhs_zero = rhs.is_small() ? (rhs.small_value() == 0) : (mpz_sgn(rhs.gmp_ptr()) == 0);
    if (rhs_zero) {
        tpy_panic("Division by zero");
    }

    if (is_small() && rhs.is_small()) {
        int64_t a = small_value();
        int64_t b = rhs.small_value();

        // Python floor division: result rounds toward negative infinity
        int64_t q = a / b;
        int64_t r = a % b;

        // Adjust for Python semantics: if remainder has opposite sign to divisor, subtract 1
        if (r != 0 && ((r < 0) != (b < 0))) {
            q -= 1;
        }

        if (fits_small(q)) {
            BigInt result;
            result.lo_ = q << 1;
            return result;
        }

        // Overflow case (rare) - use GMP
        mpz_t z;
        mpz_init_set_si(z, q);
        return from_mpz(z);
    }

    // GMP floor division (fdiv rounds toward negative infinity)
    mpz_t za, zb, result;
    to_mpz(za);
    rhs.to_mpz(zb);
    mpz_init(result);
    mpz_fdiv_q(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::floor_mod(const BigInt& rhs) const {
    // Check for division by zero
    bool rhs_zero = rhs.is_small() ? (rhs.small_value() == 0) : (mpz_sgn(rhs.gmp_ptr()) == 0);
    if (rhs_zero) {
        tpy_panic("Division by zero");
    }

    if (is_small() && rhs.is_small()) {
        int64_t a = small_value();
        int64_t b = rhs.small_value();

        // Python modulo: result has same sign as divisor
        int64_t r = a % b;

        // Adjust for Python semantics
        if (r != 0 && ((r < 0) != (b < 0))) {
            r += b;
        }

        BigInt result;
        result.lo_ = r << 1;
        return result;
    }

    // GMP floor modulo (fdiv_r gives remainder with same sign as divisor)
    mpz_t za, zb, result;
    to_mpz(za);
    rhs.to_mpz(zb);
    mpz_init(result);
    mpz_fdiv_r(result, za, zb);
    mpz_clear(za);
    mpz_clear(zb);
    return from_mpz(result);
}

inline BigInt BigInt::pow(const BigInt& exp) const {
    // Check for negative exponent (would require floats in Python)
    bool exp_negative = exp.is_small() ? (exp.small_value() < 0) : (mpz_sgn(exp.gmp_ptr()) < 0);
    if (exp_negative) {
        tpy_panic("Negative exponent not supported (would require float)");
    }

    // Fast path: small base and small exponent - avoid GMP allocation
    if (is_small() && exp.is_small()) {
        int64_t result;
        if (try_pow<int64_t>(small_value(), exp.small_value(), result) && fits_small(result)) {
            BigInt r;
            r.lo_ = result << 1;
            return r;
        }
        // Fall through to GMP on overflow
    }

    // Get exponent as unsigned long for GMP
    unsigned long exp_ul;
    if (exp.is_small()) {
        exp_ul = static_cast<unsigned long>(exp.small_value());
    } else {
        if (!mpz_fits_ulong_p(exp.gmp_ptr())) {
            tpy_panic("Exponent too large");
        }
        exp_ul = mpz_get_ui(exp.gmp_ptr());
    }

    // Compute base^exp using GMP
    mpz_t zbase, result;
    to_mpz(zbase);
    mpz_init(result);
    mpz_pow_ui(result, zbase, exp_ul);
    mpz_clear(zbase);
    return from_mpz(result);
}

/**
 * BigInt::from_float - Convert float to BigInt with NaN/inf checking.
 * Panics on NaN or infinity (Python raises ValueError/OverflowError).
 * Uses GMP's mpz_set_d to correctly handle large finite values like 1e100.
 */
inline BigInt BigInt::from_float(double v) {
    if (std::isnan(v)) {
        tpy_panic("cannot convert float NaN to integer");
    }
    if (std::isinf(v)) {
        tpy_panic("cannot convert float infinity to integer");
    }
    // mpz_set_d truncates toward zero, matching Python's int() behavior
    mpz_t result;
    mpz_init(result);
    mpz_set_d(result, v);
    return from_mpz(result);
}

/**
 * BigInt::from_str - Convert string to BigInt.
 * Panics on invalid input (Python raises ValueError).
 * Supports optional leading +/- and decimal digits only.
 */
inline BigInt BigInt::from_str(std::string_view s) {
    auto make_error = [&s]() -> std::string {
        return std::string("invalid literal for int() with base 10: '") + std::string(s) + "'";
    };

    // Skip leading whitespace
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) {
        ++start;
    }
    // Skip trailing whitespace
    size_t end = s.size();
    while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) {
        --end;
    }
    if (start >= end) {
        tpy_panic(make_error().c_str());
    }

    std::string_view trimmed = s.substr(start, end - start);

    // Check for sign
    bool negative = false;
    size_t idx = 0;
    if (trimmed[0] == '-') {
        negative = true;
        ++idx;
    } else if (trimmed[0] == '+') {
        ++idx;
    }

    if (idx >= trimmed.size()) {
        tpy_panic(make_error().c_str());
    }

    // Check all remaining chars are digits
    for (size_t i = idx; i < trimmed.size(); ++i) {
        if (!std::isdigit(static_cast<unsigned char>(trimmed[i]))) {
            tpy_panic(make_error().c_str());
        }
    }

    // Use GMP to parse the string
    mpz_t result;
    mpz_init(result);
    std::string num_str(trimmed.substr(idx));
    if (mpz_set_str(result, num_str.c_str(), 10) != 0) {
        mpz_clear(result);
        tpy_panic(make_error().c_str());
    }
    if (negative) {
        mpz_neg(result, result);
    }
    return from_mpz(result);
}

// Stream output operator for BigInt
inline std::ostream& operator<<(std::ostream& os, const BigInt& val) {
    return os << val.to_string();
}

} // namespace tpy
