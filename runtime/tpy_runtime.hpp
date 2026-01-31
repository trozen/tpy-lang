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
#include <iterator>
#include <algorithm>
#include <array>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>
#include <ranges>
#include <optional>
#include <chrono>
#include <gmp.h>

namespace tpy {

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(const char* msg) {
    std::fprintf(stderr, "TurboPython panic: %s\n", msg);
    std::exit(1);
}

/**
 * Checked pointer dereference - panics if pointer is null.
 * Used for implicit Ptr[T] -> T coercion.
 */
template <typename T>
T& deref_ptr(T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

template <typename T>
const T& deref_ptr(const T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

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

// --- Type trait for value vs reference semantics ---

/**
 * is_value_type - Type trait for determining copy vs reference semantics.
 *
 * Value types (primitives) are returned by copy when accessed from containers.
 * Object types (records, nested containers) are returned by reference.
 */
template<typename T> struct is_value_type : std::false_type {};

// Primitive value types (BigInt specialization defined after BigInt class)
template<> struct is_value_type<int32_t> : std::true_type {};
template<> struct is_value_type<int64_t> : std::true_type {};
template<> struct is_value_type<bool> : std::true_type {};
template<> struct is_value_type<char> : std::true_type {};
template<> struct is_value_type<std::string_view> : std::true_type {};

/**
 * repeat_range<T> - A range that yields elements from a sequence N times.
 *
 * Used to implement Python's list repetition: [a, b] * 3 -> [a, b, a, b, a, b]
 * Satisfies std::ranges::input_range for use with C++23 std::from_range constructors.
 * Negative counts are treated as 0 (Python semantics).
 */
template<typename T>
class repeat_range {
    std::vector<T> elements_;
    std::size_t count_;

public:
    repeat_range(int32_t count, std::initializer_list<T> elements)
        : elements_(elements), count_(count > 0 ? static_cast<std::size_t>(count) : 0) {}

    class iterator {
        const repeat_range* parent_;
        std::size_t rep_;
        std::size_t idx_;

    public:
        using iterator_category = std::input_iterator_tag;
        using value_type = T;
        using difference_type = std::ptrdiff_t;
        using pointer = const T*;
        using reference = const T&;

        iterator() : parent_(nullptr), rep_(0), idx_(0) {}
        iterator(const repeat_range* p, std::size_t r, std::size_t i)
            : parent_(p), rep_(r), idx_(i) {}

        reference operator*() const { return parent_->elements_[idx_]; }

        iterator& operator++() {
            if (++idx_ >= parent_->elements_.size()) {
                idx_ = 0;
                ++rep_;
            }
            return *this;
        }

        iterator operator++(int) { auto t = *this; ++(*this); return t; }

        bool operator==(const iterator& o) const {
            return rep_ == o.rep_ && idx_ == o.idx_;
        }
        bool operator!=(const iterator& o) const { return !(*this == o); }
    };

    iterator begin() const {
        if (count_ == 0 || elements_.empty()) return end();
        return iterator(this, 0, 0);
    }
    iterator end() const { return iterator(this, count_, 0); }

    std::size_t size() const { return count_ * elements_.size(); }
};

/**
 * Convert a range to std::vector.
 * Used for list repetition when std::from_range is unavailable (GCC < 14).
 * Pre-allocates if the range has a size() method.
 */
template<typename T, std::ranges::input_range R>
std::vector<T> to_vector(R&& range) {
    std::vector<T> result;
    if constexpr (requires { range.size(); }) {
        result.reserve(range.size());
    }
    for (auto&& elem : range) {
        result.push_back(elem);
    }
    return result;
}

/**
 * StaticList<T, N> - Fixed-capacity container with std::vector-like interface.
 *
 * No dynamic allocation. Elements are stored inline.
 *
 * NOTE: Elements are not destroyed on pop_back()/clear() - they remain alive
 * until the container is destroyed. This is fine for trivial types but diverges
 * from std::vector for types with non-trivial destructors. Future fix: use
 * aligned storage with placement new/destroy.
 */
template <typename T, std::size_t N>
class StaticList {
public:
    StaticList() noexcept : size_(0) {}

    StaticList(std::initializer_list<T> init) : size_(0) {
        if (init.size() > N) {
            tpy_panic("StaticList initializer exceeds capacity");
        }
        for (const auto& val : init) {
            data_[size_++] = val;
        }
    }

    StaticList(std::size_t count, const T& value) : size_(0) {
        if (count > N) {
            tpy_panic("StaticList fill count exceeds capacity");
        }
        for (std::size_t i = 0; i < count; ++i) {
            data_[size_++] = value;
        }
    }

    // Range constructor (C++23) - accepts any input range
    template<std::ranges::input_range R>
        requires std::convertible_to<std::ranges::range_value_t<R>, T>
    StaticList(const R& range) : size_(0) {
        for (const auto& elem : range) {
            if (size_ >= N) {
                tpy_panic("StaticList capacity exceeded");
            }
            data_[size_++] = elem;
        }
    }

    // --- std::vector-compatible interface ---

    void push_back(const T& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        data_[size_++] = value;
    }

    void push_back(T&& value) {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        data_[size_++] = std::move(value);
    }

    T pop_back() {
        if (size_ == 0) {
            tpy_panic("StaticList pop from empty list");
        }
        return std::move(data_[--size_]);
    }

    void clear() noexcept {
        size_ = 0;
    }

    T& operator[](std::size_t i) { return data_[i]; }
    const T& operator[](std::size_t i) const { return data_[i]; }

    int32_t size() const noexcept { return static_cast<int32_t>(size_); }
    static constexpr std::size_t capacity() noexcept { return N; }
    bool empty() const noexcept { return size_ == 0; }

    T* data() noexcept { return data_; }
    const T* data() const noexcept { return data_; }

    // Iterator support
    using iterator = T*;
    using const_iterator = const T*;

    iterator begin() noexcept { return data_; }
    const_iterator begin() const noexcept { return data_; }
    iterator end() noexcept { return data_ + size_; }
    const_iterator end() const noexcept { return data_ + size_; }

    // --- StaticList-specific (noalloc patterns) ---

    T* push_empty() {
        if (size_ >= N) {
            tpy_panic("StaticList capacity exceeded");
        }
        return &data_[size_++];
    }

private:
    T data_[N];
    std::size_t size_;
};

// --- Span helpers ---

template <typename T, std::size_t N>
inline std::span<const T> as_span(const std::array<T, N>& arr) {
    return std::span<const T>(arr);
}

template <typename T, std::size_t N>
inline std::span<const T> as_span(const StaticList<T, N>& list) {
    return std::span<const T>(list.data(), list.size());
}

template <typename T>
inline std::span<const T> as_span(const std::vector<T>& vec) {
    return std::span<const T>(vec.data(), vec.size());
}

template <typename T>
inline std::span<const T> as_span(std::span<const T> span) {
    return span;
}

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

    // BigInt overloads for shift (convert to int32)
    BigInt operator<<(const BigInt& shift) const { return *this << shift.to_int32(); }
    BigInt operator>>(const BigInt& shift) const { return *this >> shift.to_int32(); }

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
        if (checked_pow<int64_t>(small_value(), exp.small_value(), result) && fits_small(result)) {
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
 * normalize_index - Convert Python-style index to size_t.
 *
 * Supports negative indexing: -1 is last element, -2 is second-to-last, etc.
 * Panics if index is out of bounds.
 */
template <typename Container>
std::size_t normalize_index(const Container& c, int32_t index, const char* context) {
    std::ptrdiff_t i = index;
    if (i < 0) {
        i += static_cast<std::ptrdiff_t>(c.size());
    }
    if (i < 0 || static_cast<std::size_t>(i) >= c.size()) {
        tpy_panic(context);
    }
    return static_cast<std::size_t>(i);
}

/**
 * get_value - Get a copy of element at index (for value types).
 *
 * Use this for primitive types (Int32, BigInt, Bool, Char, str).
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
template <typename T>
T get_value(std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_value()");
    return vec[i];
}

template <typename T>
T get_value(const std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_value()");
    return vec[i];
}

/**
 * set_value - Set element at index (for value types).
 *
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 * Uses perfect forwarding to support both copy and move.
 */
template <typename T, typename V>
void set_value(std::vector<T>& vec, int32_t index, V&& value) {
    auto i = normalize_index(vec, index, "list index out of bounds in set_value()");
    vec[i] = std::forward<V>(value);
}

/**
 * get_ref - Get a reference to element at index (for object types).
 *
 * Use this for object types (records, nested containers) where
 * you need to access fields or mutate the element in-place.
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
template <typename T>
T& get_ref(std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_ref()");
    return vec[i];
}

template <typename T>
const T& get_ref(const std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_ref()");
    return vec[i];
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

// BigInt specialization of is_value_type (primary template defined earlier)
template<> struct is_value_type<BigInt> : std::true_type {};

/**
 * get_item - Unified element access for std::vector.
 *
 * Returns by value for primitive types, by reference for object types.
 * Uses is_value_type trait for compile-time dispatch.
 */
template<typename T>
decltype(auto) get_item(std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "list index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(v[i]);  // Return copy for value types
    } else {
        return (v[i]);   // Return reference for object types (parens for decltype(auto))
    }
}

template<typename T>
decltype(auto) get_item(const std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "list index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(v[i]);
    } else {
        return (v[i]);
    }
}

/**
 * set_item - Unified element assignment for std::vector.
 *
 * Sets element at index. Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 * Uses perfect forwarding to support both copy and move.
 */
template<typename T, typename V>
void set_item(std::vector<T>& v, int32_t index, V&& value) {
    auto i = normalize_index(v, index, "list index out of bounds in assignment");
    v[i] = std::forward<V>(value);
}

/**
 * get_item - Unified element access for StaticList.
 */
template<typename T, std::size_t N>
decltype(auto) get_item(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(sl[i]);
    } else {
        return (sl[i]);
    }
}

template<typename T, std::size_t N>
decltype(auto) get_item(const StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(sl[i]);
    } else {
        return (sl[i]);
    }
}

/**
 * set_item - Unified element assignment for StaticList.
 */
template<typename T, std::size_t N, typename V>
void set_item(StaticList<T, N>& sl, int32_t index, V&& value) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds in assignment");
    sl[i] = std::forward<V>(value);
}

/**
 * get_mut - Get mutable pointer to element (StaticList-specific, for noalloc patterns).
 */
template<typename T, std::size_t N>
T* get_mut(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    return &sl[i];
}

/**
 * list_insert - Python list.insert() for std::vector.
 *
 * Inserts value at index. Supports negative indexing and clamps to valid range
 * (Python semantics: -1 inserts before last element, out-of-range clamps).
 * Uses perfect forwarding to support both copy and move.
 */
template<typename T, typename V>
void list_insert(std::vector<T>& v, int32_t index, V&& value) {
    std::ptrdiff_t i = index;
    auto sz = static_cast<std::ptrdiff_t>(v.size());
    if (i < 0) {
        i += sz;
        if (i < 0) i = 0;  // Clamp to start
    } else if (i > sz) {
        i = sz;  // Clamp to end
    }
    v.insert(v.begin() + i, std::forward<V>(value));
}

/**
 * list_remove - Python list.remove() for std::vector.
 *
 * Removes first occurrence of value. Panics if not found.
 */
template<typename T>
void list_remove(std::vector<T>& v, const T& value) {
    auto it = std::find(v.begin(), v.end(), value);
    if (it == v.end()) {
        tpy_panic("list.remove(x): x not in list");
    }
    v.erase(it);
}

/**
 * list_extend - Python list.extend() for std::vector.
 *
 * Extends vector with elements from another container.
 * Overloads handle both iterator-based containers and initializer_list.
 */
template<typename T, typename Container>
void list_extend(std::vector<T>& v, const Container& other) {
    v.insert(v.end(), other.begin(), other.end());
}

template<typename T>
void list_extend(std::vector<T>& v, std::initializer_list<T> other) {
    v.insert(v.end(), other);
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
        detail::print_element(os, p.value[static_cast<std::size_t>(i)]);
    }
    os << ']';
    return os;
}

/**
 * Global<T> - Wrapper for module-level global variables.
 *
 * Defers construction of the wrapped value until assignment in the module
 * init function, ensuring proper Python-like execution order.
 */
template<typename T>
class Global {
    std::optional<T> value_;
public:
    Global() = default;

    // Disable copy/move to avoid ambiguity with T assignment
    Global(const Global&) = delete;
    Global(Global&&) = delete;
    Global& operator=(const Global&) = delete;
    Global& operator=(Global&&) = delete;

    Global& operator=(T v) {
        value_ = std::move(v);
        return *this;
    }

    operator T&() { check_init(); return *value_; }
    operator const T&() const { check_init(); return *value_; }

    T* operator->() { check_init(); return &*value_; }
    const T* operator->() const { check_init(); return &*value_; }

    T& operator*() { check_init(); return *value_; }
    const T& operator*() const { check_init(); return *value_; }

private:
    void check_init() const {
        if (!value_.has_value()) {
            tpy_panic("use of uninitialized global variable");
        }
    }
};

/**
 * time_time - Return seconds since epoch as BigInt.
 *
 * Equivalent to Python's time.time() but returns int instead of float.
 */
inline BigInt time_time() {
    auto now = std::chrono::system_clock::now();
    auto duration = now.time_since_epoch();
    auto seconds = std::chrono::duration_cast<std::chrono::seconds>(duration).count();
    return BigInt(static_cast<int64_t>(seconds));
}

} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
using tpy::BigInt;
