/**
 * TurboPython Runtime Header
 *
 * Provides the core types and utilities for TurboPython compiled code:
 * - StaticList<T, N>: Fixed-capacity container
 * - tpy_panic(): Abort on fatal error
 */

#pragma once

#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <iostream>
#include <algorithm>
#include <array>
#include <span>
#include <string>
#include <string_view>
#include <vector>
#include <gmp.h>

namespace tpy {

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(const char* msg) {
    std::fprintf(stderr, "TurboPython panic: %s\n", msg);
    std::exit(1);
}

// --- Checked power (reusable for BigInt fast path and Int32) ---

// Computes base^exp with overflow detection. Returns true on success, false on overflow.
inline bool int64_pow_checked(int64_t base, int64_t exp, int64_t& result) {
    if (exp < 0) return false;
    if (exp == 0) { result = 1; return true; }

    result = 1;
    int64_t b = base;

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

/**
 * StaticList<T, N> - Fixed-capacity container.
 *
 * No dynamic allocation. Elements are stored inline.
 * Provides append, push_empty, get, get_mut, set, and size operations.
 */
template <typename T, std::size_t N>
class StaticList {
public:
    StaticList() noexcept : size_(0) {}

    /**
     * Append a value to the list.
     * Panics if capacity is exceeded.
     */
    void append(const T& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded in append()");
        }
        data_[size_++] = value;
    }

    /**
     * Push an empty element and return a pointer to it.
     * Panics if capacity is exceeded.
     */
    T* push_empty() {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded in push_empty()");
        }
        return &data_[size_++];
    }

    /**
     * Get a reference to element at index.
     * Panics if index is out of bounds.
     */
    T& get(int32_t index) {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in get()");
        }
        return data_[i];
    }

    const T& get(int32_t index) const {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in get()");
        }
        return data_[i];
    }

    /**
     * Get a mutable pointer to element at index.
     * Panics if index is out of bounds.
     */
    T* get_mut(int32_t index) {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in get_mut()");
        }
        return &data_[i];
    }

    /**
     * Set element at index to value.
     * Panics if index is out of bounds.
     */
    void set(int32_t index, const T& value) {
        auto i = static_cast<std::size_t>(index);
        if (i >= size_) {
            tpy_panic("StaticList index out of bounds in set()");
        }
        data_[i] = value;
    }

    /**
     * Return current size.
     */
    int32_t size() const noexcept {
        return static_cast<int32_t>(size_);
    }

    /**
     * Return maximum capacity.
     */
    static constexpr std::size_t capacity() noexcept {
        return N;
    }

    /**
     * Return pointer to underlying data (for Span conversion).
     */
    T* data() noexcept {
        return data_;
    }

    const T* data() const noexcept {
        return data_;
    }

private:
    T data_[N];
    std::size_t size_;
};

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
    int32_t to_int32() const {
        if (is_small()) {
            int64_t v = small_value();
            if (v >= INT32_MIN && v <= INT32_MAX) {
                return static_cast<int32_t>(v);
            }
        }
        tpy_panic("BigInt value too large for int32_t conversion");
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

    // Check if value is zero (useful for conditionals)
    explicit operator bool() const {
        if (is_small()) {
            return small_value() != 0;
        }
        return mpz_sgn(gmp_ptr()) != 0;
    }

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
        if (int64_pow_checked(small_value(), exp.small_value(), result) && fits_small(result)) {
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
 * pop_back - Python list.pop() equivalent for std::vector.
 *
 * Removes and returns the last element. Panics if vector is empty.
 */
template <typename T>
T pop_back(std::vector<T>& v) {
    if (v.empty()) {
        tpy_panic("pop from empty list");
    }
    T result = std::move(v.back());
    v.pop_back();
    return result;
}

// --- Collection printing (Python-style: [a, b, c]) ---

template <typename T>
struct ListPrinter {
    const T& value;
    explicit ListPrinter(const T& v) : value(v) {}
};

namespace detail {

// Forward declare for recursive nested container support
template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end);

template <typename T>
void print_element(std::ostream& os, const T& elem) {
    os << elem;
}

inline void print_element(std::ostream& os, const BigInt& elem) {
    os << elem.to_string();
}

// Overloads for nested containers
template <typename T>
void print_element(std::ostream& os, const std::vector<T>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename T, std::size_t N>
void print_element(std::ostream& os, const std::array<T, N>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end) {
    os << '[';
    bool first = true;
    for (auto it = begin; it != end; ++it) {
        if (!first) os << ", ";
        first = false;
        print_element(os, *it);
    }
    os << ']';
}

} // namespace detail

// Stream output operator for BigInt
inline std::ostream& operator<<(std::ostream& os, const BigInt& val) {
    return os << val.to_string();
}

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::vector<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

template <typename T, std::size_t N>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::array<T, N>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::span<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

template <typename T, std::size_t N>
std::ostream& operator<<(std::ostream& os, const ListPrinter<StaticList<T, N>>& p) {
    os << '[';
    for (int32_t i = 0; i < p.value.size(); ++i) {
        if (i > 0) os << ", ";
        detail::print_element(os, p.value.get(i));
    }
    os << ']';
    return os;
}

} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
using tpy::BigInt;
