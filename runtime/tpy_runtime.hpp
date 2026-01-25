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
#include <array>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace tpy {

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(const char* msg) {
    std::fprintf(stderr, "TurboPython panic: %s\n", msg);
    std::abort();
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
 * - Tag in lo_ & 1: 0 = small, 1 = big (heap allocated)
 * - Small: value stored as lo_ >> 1 (63-bit signed range)
 * - Big: lo_ has size|tag, hi_ is pointer to uint64_t[] limbs
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
            init_from_large(v);
        }
    }

    BigInt(const BigInt& other) noexcept {
        if (other.is_small()) {
            lo_ = other.lo_;
            hi_ = 0;
        } else {
            copy_big(other);
        }
    }

    BigInt(BigInt&& other) noexcept : lo_(other.lo_), hi_(other.hi_) {
        other.lo_ = 0;
        other.hi_ = 0;
    }

    ~BigInt() {
        free_big();
    }

    BigInt& operator=(const BigInt& other) noexcept {
        if (this != &other) {
            free_big();
            if (other.is_small()) {
                lo_ = other.lo_;
                hi_ = 0;
            } else {
                copy_big(other);
            }
        }
        return *this;
    }

    BigInt& operator=(BigInt&& other) noexcept {
        if (this != &other) {
            free_big();
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
            return add_big(*this, rhs);
        }
        return add_big(*this, rhs);
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
            return sub_big(*this, rhs);
        }
        return sub_big(*this, rhs);
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
            return mul_big(*this, rhs);
        }
        return mul_big(*this, rhs);
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
        return neg_big(*this);
    }

    // Compound assignment operators
    BigInt& operator+=(const BigInt& rhs) { *this = *this + rhs; return *this; }
    BigInt& operator-=(const BigInt& rhs) { *this = *this - rhs; return *this; }
    BigInt& operator*=(const BigInt& rhs) { *this = *this * rhs; return *this; }
    BigInt& operator/=(const BigInt& rhs) { *this = *this / rhs; return *this; }
    BigInt& operator%=(const BigInt& rhs) { *this = *this % rhs; return *this; }

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
        return big_to_string();
    }

    // Check if value is zero (useful for conditionals)
    explicit operator bool() const {
        if (is_small()) {
            return small_value() != 0;
        }
        return !is_big_zero();
    }

private:
    int64_t lo_;  // bit 0: tag. small: value << 1. big: size | 1
    int64_t hi_;  // small: unused. big: pointer to uint64_t[]

    static constexpr int64_t SMALL_MAX = (INT64_MAX >> 1);
    static constexpr int64_t SMALL_MIN = (INT64_MIN >> 1);

    static bool fits_small(int64_t v) noexcept {
        return v >= SMALL_MIN && v <= SMALL_MAX;
    }

    void init_from_large(int64_t v) {
        // Allocate space for 1-2 limbs
        bool negative = v < 0;
        uint64_t abs_val = negative ? (v == INT64_MIN ? static_cast<uint64_t>(INT64_MAX) + 1 : static_cast<uint64_t>(-v)) : static_cast<uint64_t>(v);

        auto* limbs = new uint64_t[2];
        limbs[0] = abs_val;
        limbs[1] = 0;

        int32_t size = (abs_val > 0) ? 1 : 0;
        if (negative) size = -size;

        lo_ = (static_cast<int64_t>(size) << 32) | 1;
        hi_ = reinterpret_cast<int64_t>(limbs);
    }

    void free_big() noexcept {
        if (!is_small() && hi_ != 0) {
            delete[] reinterpret_cast<uint64_t*>(hi_);
        }
    }

    void copy_big(const BigInt& other) {
        int32_t size = static_cast<int32_t>(other.lo_ >> 32);
        int32_t abs_size = size < 0 ? -size : size;
        int32_t alloc = abs_size > 0 ? abs_size : 1;

        auto* limbs = new uint64_t[alloc];
        auto* src = reinterpret_cast<uint64_t*>(other.hi_);
        for (int32_t i = 0; i < abs_size; ++i) {
            limbs[i] = src[i];
        }

        lo_ = other.lo_;
        hi_ = reinterpret_cast<int64_t>(limbs);
    }

    int32_t big_size() const noexcept {
        return static_cast<int32_t>(lo_ >> 32);
    }

    uint64_t* big_limbs() const noexcept {
        return reinterpret_cast<uint64_t*>(hi_);
    }

    bool is_big_zero() const {
        return big_size() == 0;
    }

    int compare(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            return (a > b) - (a < b);
        }

        // Convert both to sign and magnitude for comparison
        bool neg_a = is_negative();
        bool neg_b = rhs.is_negative();

        if (neg_a != neg_b) {
            return neg_a ? -1 : 1;
        }

        int mag_cmp = compare_magnitude(rhs);
        return neg_a ? -mag_cmp : mag_cmp;
    }

    bool is_negative() const {
        if (is_small()) {
            return small_value() < 0;
        }
        return big_size() < 0;
    }

    int compare_magnitude(const BigInt& rhs) const {
        int32_t size_a = is_small() ? 1 : (big_size() < 0 ? -big_size() : big_size());
        int32_t size_b = rhs.is_small() ? 1 : (rhs.big_size() < 0 ? -rhs.big_size() : rhs.big_size());

        // Handle zero cases
        if (is_small() && small_value() == 0) size_a = 0;
        if (rhs.is_small() && rhs.small_value() == 0) size_b = 0;

        if (size_a != size_b) {
            return (size_a > size_b) - (size_a < size_b);
        }

        // Same size, compare limbs from most significant
        for (int32_t i = size_a - 1; i >= 0; --i) {
            uint64_t limb_a = get_limb(i);
            uint64_t limb_b = rhs.get_limb(i);
            if (limb_a != limb_b) {
                return (limb_a > limb_b) - (limb_a < limb_b);
            }
        }
        return 0;
    }

    uint64_t get_limb(int32_t idx) const {
        if (is_small()) {
            if (idx == 0) {
                int64_t v = small_value();
                return v < 0 ? static_cast<uint64_t>(-v) : static_cast<uint64_t>(v);
            }
            return 0;
        }
        int32_t abs_size = big_size() < 0 ? -big_size() : big_size();
        if (idx < abs_size) {
            return big_limbs()[idx];
        }
        return 0;
    }

    // Big integer arithmetic helpers
    static BigInt add_big(const BigInt& a, const BigInt& b);
    static BigInt sub_big(const BigInt& a, const BigInt& b);
    static BigInt mul_big(const BigInt& a, const BigInt& b);
    static BigInt neg_big(const BigInt& a);

    BigInt floor_div(const BigInt& rhs) const;
    BigInt floor_mod(const BigInt& rhs) const;

    std::string big_to_string() const;
};

// Implementation of big integer operations

inline BigInt BigInt::add_big(const BigInt& a, const BigInt& b) {
    // For simplicity, convert to 128-bit arithmetic for medium values
    // This handles the common case of values that overflow 63 bits but fit in 128
    int64_t av = a.is_small() ? a.small_value() : 0;
    int64_t bv = b.is_small() ? b.small_value() : 0;

    if (a.is_small() && b.is_small()) {
        // Use 128-bit arithmetic
        __int128 result = static_cast<__int128>(av) + static_cast<__int128>(bv);
        if (result >= SMALL_MIN && result <= SMALL_MAX) {
            BigInt r;
            r.lo_ = static_cast<int64_t>(result) << 1;
            return r;
        }
        // Result doesn't fit in small, create big
        BigInt r;
        bool negative = result < 0;
        unsigned __int128 abs_result = negative ? -result : result;

        auto* limbs = new uint64_t[2];
        limbs[0] = static_cast<uint64_t>(abs_result);
        limbs[1] = static_cast<uint64_t>(abs_result >> 64);

        int32_t size = limbs[1] ? 2 : 1;
        if (negative) size = -size;

        r.lo_ = (static_cast<int64_t>(size) << 32) | 1;
        r.hi_ = reinterpret_cast<int64_t>(limbs);
        return r;
    }

    // Full big integer addition (for very large numbers)
    tpy_panic("BigInt overflow: numbers too large for current implementation");
}

inline BigInt BigInt::sub_big(const BigInt& a, const BigInt& b) {
    int64_t av = a.is_small() ? a.small_value() : 0;
    int64_t bv = b.is_small() ? b.small_value() : 0;

    if (a.is_small() && b.is_small()) {
        __int128 result = static_cast<__int128>(av) - static_cast<__int128>(bv);
        if (result >= SMALL_MIN && result <= SMALL_MAX) {
            BigInt r;
            r.lo_ = static_cast<int64_t>(result) << 1;
            return r;
        }
        BigInt r;
        bool negative = result < 0;
        unsigned __int128 abs_result = negative ? -result : result;

        auto* limbs = new uint64_t[2];
        limbs[0] = static_cast<uint64_t>(abs_result);
        limbs[1] = static_cast<uint64_t>(abs_result >> 64);

        int32_t size = limbs[1] ? 2 : 1;
        if (negative) size = -size;

        r.lo_ = (static_cast<int64_t>(size) << 32) | 1;
        r.hi_ = reinterpret_cast<int64_t>(limbs);
        return r;
    }

    tpy_panic("BigInt overflow: numbers too large for current implementation");
}

inline BigInt BigInt::mul_big(const BigInt& a, const BigInt& b) {
    int64_t av = a.is_small() ? a.small_value() : 0;
    int64_t bv = b.is_small() ? b.small_value() : 0;

    if (a.is_small() && b.is_small()) {
        __int128 result = static_cast<__int128>(av) * static_cast<__int128>(bv);
        if (result >= SMALL_MIN && result <= SMALL_MAX) {
            BigInt r;
            r.lo_ = static_cast<int64_t>(result) << 1;
            return r;
        }
        BigInt r;
        bool negative = result < 0;
        unsigned __int128 abs_result = negative ? -result : result;

        auto* limbs = new uint64_t[2];
        limbs[0] = static_cast<uint64_t>(abs_result);
        limbs[1] = static_cast<uint64_t>(abs_result >> 64);

        int32_t size = limbs[1] ? 2 : 1;
        if (negative) size = -size;

        r.lo_ = (static_cast<int64_t>(size) << 32) | 1;
        r.hi_ = reinterpret_cast<int64_t>(limbs);
        return r;
    }

    tpy_panic("BigInt overflow: numbers too large for current implementation");
}

inline BigInt BigInt::neg_big(const BigInt& a) {
    if (a.is_small()) {
        int64_t v = a.small_value();
        // Handle INT64_MIN edge case
        if (v == INT64_MIN) {
            // -INT64_MIN doesn't fit in int64_t, need big representation
            BigInt r;
            auto* limbs = new uint64_t[2];
            limbs[0] = static_cast<uint64_t>(INT64_MAX) + 1;
            limbs[1] = 0;
            r.lo_ = (1LL << 32) | 1;  // size = 1 (positive)
            r.hi_ = reinterpret_cast<int64_t>(limbs);
            return r;
        }
        BigInt r;
        r.lo_ = (-v) << 1;
        return r;
    }

    // Negate big: just flip the sign
    BigInt r;
    r.copy_big(a);
    int32_t size = r.big_size();
    r.lo_ = (static_cast<int64_t>(-size) << 32) | 1;
    return r;
}

inline BigInt BigInt::floor_div(const BigInt& rhs) const {
    // Check for division by zero
    if ((rhs.is_small() && rhs.small_value() == 0) ||
        (!rhs.is_small() && rhs.is_big_zero())) {
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

        // Overflow case (rare)
        BigInt result;
        result.init_from_large(q);
        return result;
    }

    tpy_panic("BigInt division: numbers too large for current implementation");
}

inline BigInt BigInt::floor_mod(const BigInt& rhs) const {
    // Check for division by zero
    if ((rhs.is_small() && rhs.small_value() == 0) ||
        (!rhs.is_small() && rhs.is_big_zero())) {
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

    tpy_panic("BigInt modulo: numbers too large for current implementation");
}

inline std::string BigInt::big_to_string() const {
    if (is_big_zero()) {
        return "0";
    }

    int32_t size = big_size();
    bool negative = size < 0;
    int32_t abs_size = negative ? -size : size;
    uint64_t* limbs = big_limbs();

    // For 1-2 limb numbers, use 128-bit arithmetic for string conversion
    if (abs_size <= 2) {
        unsigned __int128 val = limbs[0];
        if (abs_size == 2) {
            val |= (static_cast<unsigned __int128>(limbs[1]) << 64);
        }

        std::string result;
        while (val > 0) {
            result = char('0' + val % 10) + result;
            val /= 10;
        }

        if (negative) {
            result = "-" + result;
        }
        return result;
    }

    tpy_panic("BigInt to_string: number too large for current implementation");
}

} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
using tpy::BigInt;
