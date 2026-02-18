/**
 * TurboPython Runtime - BigInt
 *
 * Arbitrary precision integer with small-int optimization.
 * Uses pointer tagging to store small integers inline.
 *
 * TODO(perf):
 * - Reduce big-path temporary allocations by adding in-place limb operations
 *   and reusable scratch buffers for add/sub/mul/div flows.
 * - Replace shift/subtract long division with a faster normalized long division
 *   algorithm (and optional Burnikel-Ziegler for very large operands).
 * - Avoid full two's-complement materialization for bitwise ops when possible.
 * - Replace to_double() decimal-string roundtrip with direct binary conversion.
 * - Add architecture-specific kernels (e.g. BMI2/ADX intrinsics) behind a
 *   clean abstraction; keep scalar path as baseline.
 */

#pragma once

#include <bit>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <ostream>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "core.hpp"
#include "fixed_int.hpp"
#include "type_traits.hpp"

namespace tpy {

/**
 * BigInt - Arbitrary precision integer with small-int optimization.
 *
 * raw_ layout:
 * - raw_ & 1 == 0: small value (int63) stored as (value << 1)
 * - raw_ & 1 == 1: tagged pointer to HeapBig
 *
 * Python semantics for division and modulo (floor division).
 */
class BigInt {
public:
    BigInt() noexcept : raw_(0) {}

    BigInt(int32_t v) noexcept : BigInt(static_cast<int64_t>(v)) {}

    BigInt(int64_t v) noexcept {
        if (fits_small(v)) {
            raw_ = encode_small(v);
            return;
        }
        auto* p = alloc_heap(1);
        p->len = 1;
        p->sign = (v < 0) ? -1 : 1;
        p->limbs[0] = abs_u64(v);
        raw_ = encode_big(p);
    }

    BigInt(uint64_t v) noexcept {
        if (v <= static_cast<uint64_t>(SMALL_MAX)) {
            raw_ = encode_small(static_cast<int64_t>(v));
            return;
        }
        auto* p = alloc_heap(1);
        p->len = 1;
        p->sign = 1;
        p->limbs[0] = v;
        raw_ = encode_big(p);
    }

    BigInt(const BigInt& other) noexcept {
        if (other.is_small()) {
            raw_ = other.raw_;
            return;
        }
        raw_ = encode_big(clone_heap(other.heap_ptr()));
    }

    BigInt(BigInt&& other) noexcept : raw_(other.raw_) {
        other.raw_ = 0;
    }

    ~BigInt() {
        if (!is_small()) {
            free_heap(heap_ptr());
        }
    }

    BigInt& operator=(const BigInt& other) noexcept {
        if (this == &other) {
            return *this;
        }
        if (!is_small()) {
            free_heap(heap_ptr());
        }
        if (other.is_small()) {
            raw_ = other.raw_;
        } else {
            raw_ = encode_big(clone_heap(other.heap_ptr()));
        }
        return *this;
    }

    BigInt& operator=(BigInt&& other) noexcept {
        if (this == &other) {
            return *this;
        }
        if (!is_small()) {
            free_heap(heap_ptr());
        }
        raw_ = other.raw_;
        other.raw_ = 0;
        return *this;
    }

    bool is_small() const noexcept { return (raw_ & TAG_MASK) == 0; }

    int64_t small_value() const noexcept {
        return static_cast<int64_t>(raw_) >> 1;
    }

    // Arithmetic operators
    BigInt operator+(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_add_overflow(a, b, &result) && fits_small(result)) {
                return make_small(result);
            }
        }

        const int a_sign = signum();
        const int b_sign = rhs.signum();
        if (a_sign == 0) return rhs;
        if (b_sign == 0) return *this;

        std::vector<uint64_t> a = abs_limbs();
        std::vector<uint64_t> b = rhs.abs_limbs();

        if (a_sign == b_sign) {
            return from_sign_mag(a_sign, add_mag(a, b));
        }

        int cmp = cmp_mag(a, b);
        if (cmp == 0) {
            return BigInt(0);
        }
        if (cmp > 0) {
            return from_sign_mag(a_sign, sub_mag(a, b));
        }
        return from_sign_mag(b_sign, sub_mag(b, a));
    }

    BigInt operator-(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_sub_overflow(a, b, &result) && fits_small(result)) {
                return make_small(result);
            }
        }
        return *this + (-rhs);
    }

    BigInt operator*(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            int64_t a = small_value();
            int64_t b = rhs.small_value();
            int64_t result;
            if (!__builtin_mul_overflow(a, b, &result) && fits_small(result)) {
                return make_small(result);
            }
        }

        const int a_sign = signum();
        const int b_sign = rhs.signum();
        if (a_sign == 0 || b_sign == 0) return BigInt(0);

        std::vector<uint64_t> a = abs_limbs();
        std::vector<uint64_t> b = rhs.abs_limbs();
        std::vector<uint64_t> prod = mul_mag(a, b);
        return from_sign_mag(a_sign == b_sign ? 1 : -1, std::move(prod));
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
                return make_small(-v);
            }
        }
        const int s = signum();
        if (s == 0) {
            return BigInt(0);
        }
        return from_sign_mag(-s, abs_limbs());
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
        if (shift == 0 || signum() == 0) {
            return *this;
        }
        std::vector<uint64_t> mag = abs_limbs();
        return from_sign_mag(signum(), lshift_mag(mag, static_cast<size_t>(shift)));
    }

    BigInt operator>>(int32_t shift) const {
        if (shift < 0) {
            tpy_panic("Negative shift count");
        }
        if (shift == 0 || signum() == 0) {
            return *this;
        }
        BigInt divisor = BigInt(1) << shift;
        return floor_div(divisor);
    }

    BigInt& operator<<=(int32_t shift) { *this = *this << shift; return *this; }
    BigInt& operator>>=(int32_t shift) { *this = *this >> shift; return *this; }

    // BigInt overloads for shift (convert to int32)
    BigInt operator<<(const BigInt& shift) const { return *this << shift.to_fixed_check<int32_t>(); }
    BigInt operator>>(const BigInt& shift) const { return *this >> shift.to_fixed_check<int32_t>(); }

    // Bitwise operators
    BigInt operator&(const BigInt& rhs) const {
        return bitwise_binary(rhs, '&');
    }

    BigInt operator|(const BigInt& rhs) const {
        return bitwise_binary(rhs, '|');
    }

    BigInt operator^(const BigInt& rhs) const {
        return bitwise_binary(rhs, '^');
    }

    BigInt operator~() const {
        // Python: ~x = -(x+1)
        return -(*this + BigInt(1));
    }

    BigInt& operator&=(const BigInt& rhs) { *this = *this & rhs; return *this; }
    BigInt& operator|=(const BigInt& rhs) { *this = *this | rhs; return *this; }
    BigInt& operator^=(const BigInt& rhs) { *this = *this ^ rhs; return *this; }

    // Power operator (Python semantics: negative exponent not supported)
    BigInt pow(const BigInt& exp) const {
        if (exp.signum() < 0) {
            tpy_panic("Negative exponent not supported (would require float)");
        }

        uint64_t e = 0;
        if (!exp.to_uint64_checked(e)) {
            tpy_panic("Exponent too large");
        }

        BigInt base = *this;
        BigInt result(1);
        while (e != 0) {
            if ((e & 1U) != 0) {
                result *= base;
            }
            e >>= 1U;
            if (e != 0) {
                base *= base;
            }
        }
        return result;
    }

    // Comparison operators
    bool operator==(const BigInt& rhs) const {
        if (is_small() && rhs.is_small()) {
            return raw_ == rhs.raw_;
        }
        return compare(rhs) == 0;
    }

    bool operator!=(const BigInt& rhs) const { return !(*this == rhs); }

    bool operator<(const BigInt& rhs) const { return compare(rhs) < 0; }
    bool operator<=(const BigInt& rhs) const { return compare(rhs) <= 0; }
    bool operator>(const BigInt& rhs) const { return compare(rhs) > 0; }
    bool operator>=(const BigInt& rhs) const { return compare(rhs) >= 0; }

    // Generic conversion to any fixed-width integer type
    template<typename T>
    T to_fixed_check() const {
        static_assert(std::is_integral_v<T>, "T must be an integer type");

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
            std::string msg = std::string(tpy::fixed_int_name<T>()) + " overflow: value out of range";
            tpy_panic(msg.c_str());
        }

        const HeapBig* p = heap_ptr();
        if (p->len > 1) {
            std::string msg = std::string(tpy::fixed_int_name<T>()) + " overflow: value out of range";
            tpy_panic(msg.c_str());
        }

        uint64_t mag = (p->len == 0) ? 0 : p->limbs[0];
        if constexpr (std::is_signed_v<T>) {
            __int128 value = (p->sign < 0)
                ? -static_cast<__int128>(mag)
                : static_cast<__int128>(mag);
            __int128 min_v = static_cast<__int128>(std::numeric_limits<T>::min());
            __int128 max_v = static_cast<__int128>(std::numeric_limits<T>::max());
            if (value >= min_v && value <= max_v) {
                return static_cast<T>(value);
            }
        } else {
            if (p->sign >= 0 && mag <= static_cast<uint64_t>(std::numeric_limits<T>::max())) {
                return static_cast<T>(mag);
            }
        }

        std::string msg = std::string(tpy::fixed_int_name<T>()) + " overflow: value out of range";
        tpy_panic(msg.c_str());
    }

    // Truncating conversion to any fixed-width integer type (modular reduction, never panics)
    template<typename T>
    T to_fixed_trunc() const {
        static_assert(std::is_integral_v<T>, "T must be an integer type");
        constexpr int bits = static_cast<int>(sizeof(T) * 8);

        uint64_t low = 0;
        if (is_small()) {
            low = static_cast<uint64_t>(small_value());
            return static_cast<T>(low);
        }

        const HeapBig* p = heap_ptr();
        uint64_t mag_mod = 0;
        if (p->len != 0) {
            mag_mod = p->limbs[0];
        }

        if constexpr (bits < 64) {
            uint64_t mask = (uint64_t(1) << bits) - 1;
            mag_mod &= mask;
            if (p->sign < 0) {
                low = (uint64_t(0) - mag_mod) & mask;
            } else {
                low = mag_mod;
            }
        } else {
            if (p->sign < 0) {
                low = uint64_t(0) - mag_mod;
            } else {
                low = mag_mod;
            }
        }

        return static_cast<T>(low);
    }

    // Conversion to double (for float operations)
    double to_double() const {
        if (is_small()) {
            return static_cast<double>(small_value());
        }
        std::string s = to_string();
        return std::strtod(s.c_str(), nullptr);
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

        const HeapBig* p = heap_ptr();
        if (p->len == 0) {
            return "0";
        }

        std::vector<uint64_t> tmp(p->limbs, p->limbs + p->len);
        std::vector<uint32_t> chunks;
        chunks.reserve((p->len * 64) / 29 + 2);

        while (!tmp.empty()) {
            uint32_t rem = div_small_inplace(tmp, 1000000000U);
            chunks.push_back(rem);
        }

        std::string out;
        if (p->sign < 0) {
            out.push_back('-');
        }

        out += std::to_string(chunks.back());
        for (size_t i = chunks.size(); i > 1; --i) {
            size_t idx = i - 2;
            out += pad9(chunks[idx]);
        }
        return out;
    }

    // Absolute value (static method)
    static BigInt abs(const BigInt& x) {
        return x < BigInt(0) ? -x : x;
    }

    // Check if value is zero (useful for conditionals)
    explicit operator bool() const {
        return signum() != 0;
    }

    // Static factory methods for conversions
    static BigInt from_float(double v) {
        if (std::isnan(v)) {
            tpy_panic("cannot convert float NaN to integer");
        }
        if (std::isinf(v)) {
            tpy_panic("cannot convert float infinity to integer");
        }
        if (v == 0.0) {
            return BigInt(0);
        }

        bool negative = v < 0.0;
        double av = negative ? -v : v;

        uint64_t bits = std::bit_cast<uint64_t>(av);
        uint64_t exp_bits = (bits >> 52) & 0x7FFU;
        uint64_t frac = bits & ((uint64_t(1) << 52) - 1);

        // Subnormal (and |v| < 1) truncates to 0.
        if (exp_bits == 0) {
            return BigInt(0);
        }

        int exp = static_cast<int>(exp_bits) - 1023;
        if (exp < 0) {
            return BigInt(0);
        }

        uint64_t mantissa = frac | (uint64_t(1) << 52);
        std::vector<uint64_t> mag;

        if (exp >= 52) {
            mag.push_back(mantissa);
            mag = lshift_mag(mag, static_cast<size_t>(exp - 52));
        } else {
            uint64_t truncated = mantissa >> static_cast<unsigned>(52 - exp);
            if (truncated != 0) {
                mag.push_back(truncated);
            }
        }

        return from_sign_mag(negative ? -1 : 1, std::move(mag));
    }

    static BigInt from_str(std::string_view s) {
        auto make_error = [&s]() -> std::string {
            return std::string("invalid literal for int() with base 10: '") + std::string(s) + "'";
        };

        // Skip leading whitespace.
        size_t start = 0;
        while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) {
            ++start;
        }
        // Skip trailing whitespace.
        size_t end = s.size();
        while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) {
            --end;
        }
        if (start >= end) {
            tpy_panic(make_error().c_str());
        }

        std::string_view trimmed = s.substr(start, end - start);

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

        std::vector<uint64_t> mag;
        for (size_t i = idx; i < trimmed.size(); ++i) {
            unsigned char ch = static_cast<unsigned char>(trimmed[i]);
            if (!std::isdigit(ch)) {
                tpy_panic(make_error().c_str());
            }
            mul_small_inplace(mag, 10);
            add_small_inplace(mag, static_cast<uint32_t>(ch - '0'));
        }

        return from_sign_mag(negative ? -1 : 1, std::move(mag));
    }

private:
    struct HeapBig {
        uint32_t len;     // Number of used limbs.
        uint32_t cap;     // Allocated limb capacity.
        int8_t sign;      // -1, 0, +1
        uint8_t _pad[7];
        uint64_t limbs[1];  // Trailing storage.
    };

    uintptr_t raw_;  // small: encoded int63; big: tagged HeapBig* (LSB=1)

    static_assert(sizeof(uintptr_t) == 8, "BigInt requires 64-bit pointers");
    static_assert(alignof(HeapBig) >= 2, "BigInt pointer tagging requires aligned HeapBig");

    static constexpr uintptr_t TAG_MASK = uintptr_t(1);
    static constexpr int64_t SMALL_MAX = (INT64_MAX >> 1);
    static constexpr int64_t SMALL_MIN = (INT64_MIN >> 1);
    static constexpr uint64_t SMALL_MIN_MAG = (uint64_t(1) << 62);

    static bool fits_small(int64_t v) noexcept {
        return v >= SMALL_MIN && v <= SMALL_MAX;
    }

    static uintptr_t encode_small(int64_t v) noexcept {
        return static_cast<uintptr_t>(static_cast<uint64_t>(v) << 1);
    }

    static uintptr_t encode_big(HeapBig* p) noexcept {
        return reinterpret_cast<uintptr_t>(p) | TAG_MASK;
    }

    static BigInt make_small(int64_t v) noexcept {
        BigInt out;
        out.raw_ = encode_small(v);
        return out;
    }

    static uint64_t abs_u64(int64_t v) noexcept {
        uint64_t u = static_cast<uint64_t>(v);
        return (v < 0) ? (~u + 1U) : u;
    }

    HeapBig* heap_ptr() noexcept {
        return reinterpret_cast<HeapBig*>(raw_ & ~TAG_MASK);
    }

    const HeapBig* heap_ptr() const noexcept {
        return reinterpret_cast<const HeapBig*>(raw_ & ~TAG_MASK);
    }

    static size_t heap_alloc_size(uint32_t cap) noexcept {
        size_t base = offsetof(HeapBig, limbs);
        return base + static_cast<size_t>(cap) * sizeof(uint64_t);
    }

    static HeapBig* alloc_heap(uint32_t cap) {
        if (cap == 0) {
            cap = 1;
        }
        void* mem = std::malloc(heap_alloc_size(cap));
        if (mem == nullptr) {
            tpy_panic("out of memory");
        }
        auto* p = static_cast<HeapBig*>(mem);
        p->len = 0;
        p->cap = cap;
        p->sign = 0;
        for (uint32_t i = 0; i < cap; ++i) {
            p->limbs[i] = 0;
        }
        return p;
    }

    static HeapBig* clone_heap(const HeapBig* src) {
        HeapBig* dst = alloc_heap(src->len == 0 ? 1 : src->len);
        dst->len = src->len;
        dst->cap = src->len == 0 ? 1 : src->len;
        dst->sign = src->sign;
        for (uint32_t i = 0; i < src->len; ++i) {
            dst->limbs[i] = src->limbs[i];
        }
        return dst;
    }

    static void free_heap(HeapBig* p) noexcept {
        std::free(p);
    }

    int signum() const noexcept {
        if (is_small()) {
            int64_t v = small_value();
            return (v > 0) - (v < 0);
        }
        return heap_ptr()->sign;
    }

    static void trim_mag(std::vector<uint64_t>& limbs) {
        while (!limbs.empty() && limbs.back() == 0) {
            limbs.pop_back();
        }
    }

    std::vector<uint64_t> abs_limbs() const {
        if (is_small()) {
            int64_t v = small_value();
            if (v == 0) {
                return {};
            }
            return {abs_u64(v)};
        }
        const HeapBig* p = heap_ptr();
        return std::vector<uint64_t>(p->limbs, p->limbs + p->len);
    }

    static BigInt from_sign_mag(int sign, std::vector<uint64_t> limbs) {
        trim_mag(limbs);
        if (limbs.empty() || sign == 0) {
            return BigInt(0);
        }

        sign = (sign < 0) ? -1 : 1;

        if (limbs.size() == 1) {
            uint64_t v = limbs[0];
            if (sign > 0) {
                if (v <= static_cast<uint64_t>(SMALL_MAX)) {
                    return make_small(static_cast<int64_t>(v));
                }
            } else {
                if (v <= SMALL_MIN_MAG) {
                    if (v == SMALL_MIN_MAG) {
                        return make_small(SMALL_MIN);
                    }
                    return make_small(-static_cast<int64_t>(v));
                }
            }
        }

        HeapBig* p = alloc_heap(static_cast<uint32_t>(limbs.size()));
        p->len = static_cast<uint32_t>(limbs.size());
        p->sign = static_cast<int8_t>(sign);
        for (size_t i = 0; i < limbs.size(); ++i) {
            p->limbs[i] = limbs[i];
        }

        BigInt out;
        out.raw_ = encode_big(p);
        return out;
    }

    static int cmp_mag(const std::vector<uint64_t>& a, const std::vector<uint64_t>& b) {
        if (a.size() != b.size()) {
            return (a.size() > b.size()) ? 1 : -1;
        }
        for (size_t i = a.size(); i > 0; --i) {
            size_t idx = i - 1;
            if (a[idx] != b[idx]) {
                return (a[idx] > b[idx]) ? 1 : -1;
            }
        }
        return 0;
    }

    static std::vector<uint64_t> add_mag(const std::vector<uint64_t>& a, const std::vector<uint64_t>& b) {
        size_t n = (a.size() > b.size()) ? a.size() : b.size();
        std::vector<uint64_t> out(n, 0);
        unsigned __int128 carry = 0;
        for (size_t i = 0; i < n; ++i) {
            unsigned __int128 av = (i < a.size()) ? a[i] : 0;
            unsigned __int128 bv = (i < b.size()) ? b[i] : 0;
            unsigned __int128 sum = av + bv + carry;
            out[i] = static_cast<uint64_t>(sum);
            carry = sum >> 64;
        }
        if (carry != 0) {
            out.push_back(static_cast<uint64_t>(carry));
        }
        trim_mag(out);
        return out;
    }

    static std::vector<uint64_t> sub_mag(const std::vector<uint64_t>& a, const std::vector<uint64_t>& b) {
        // Precondition: a >= b
        std::vector<uint64_t> out(a.size(), 0);
        uint64_t borrow = 0;
        for (size_t i = 0; i < a.size(); ++i) {
            unsigned __int128 av = a[i];
            unsigned __int128 bv = (i < b.size()) ? b[i] : 0;
            unsigned __int128 sub = bv + borrow;
            if (av >= sub) {
                out[i] = static_cast<uint64_t>(av - sub);
                borrow = 0;
            } else {
                out[i] = static_cast<uint64_t>((static_cast<unsigned __int128>(uint64_t(1)) << 64) + av - sub);
                borrow = 1;
            }
        }
        trim_mag(out);
        return out;
    }

    static std::vector<uint64_t> mul_mag(const std::vector<uint64_t>& a, const std::vector<uint64_t>& b) {
        if (a.empty() || b.empty()) {
            return {};
        }
        std::vector<uint64_t> out(a.size() + b.size() + 1, 0);
        for (size_t i = 0; i < a.size(); ++i) {
            unsigned __int128 carry = 0;
            for (size_t j = 0; j < b.size(); ++j) {
                size_t idx = i + j;
                unsigned __int128 cur =
                    static_cast<unsigned __int128>(a[i]) * b[j] +
                    out[idx] + carry;
                out[idx] = static_cast<uint64_t>(cur);
                carry = cur >> 64;
            }
            size_t k = i + b.size();
            while (carry != 0) {
                unsigned __int128 cur = static_cast<unsigned __int128>(out[k]) + carry;
                out[k] = static_cast<uint64_t>(cur);
                carry = cur >> 64;
                ++k;
            }
        }
        trim_mag(out);
        return out;
    }

    static void add_small_inplace(std::vector<uint64_t>& a, uint32_t v) {
        if (v == 0) {
            return;
        }
        unsigned __int128 carry = v;
        size_t i = 0;
        while (carry != 0) {
            if (i >= a.size()) {
                a.push_back(0);
            }
            unsigned __int128 sum = static_cast<unsigned __int128>(a[i]) + carry;
            a[i] = static_cast<uint64_t>(sum);
            carry = sum >> 64;
            ++i;
        }
    }

    static void mul_small_inplace(std::vector<uint64_t>& a, uint32_t m) {
        if (a.empty() || m == 1) {
            return;
        }
        if (m == 0) {
            a.clear();
            return;
        }
        unsigned __int128 carry = 0;
        for (size_t i = 0; i < a.size(); ++i) {
            unsigned __int128 prod = static_cast<unsigned __int128>(a[i]) * m + carry;
            a[i] = static_cast<uint64_t>(prod);
            carry = prod >> 64;
        }
        if (carry != 0) {
            a.push_back(static_cast<uint64_t>(carry));
        }
    }

    static std::vector<uint64_t> lshift_mag(const std::vector<uint64_t>& a, size_t bits) {
        if (a.empty() || bits == 0) {
            return a;
        }
        size_t word_shift = bits / 64;
        size_t bit_shift = bits % 64;

        std::vector<uint64_t> out;
        out.reserve(a.size() + word_shift + 1);
        for (size_t i = 0; i < word_shift; ++i) {
            out.push_back(0);
        }

        if (bit_shift == 0) {
            out.insert(out.end(), a.begin(), a.end());
            trim_mag(out);
            return out;
        }

        uint64_t carry = 0;
        for (uint64_t limb : a) {
            uint64_t lower = (limb << bit_shift) | carry;
            out.push_back(lower);
            carry = limb >> (64 - bit_shift);
        }
        if (carry != 0) {
            out.push_back(carry);
        }
        trim_mag(out);
        return out;
    }

    static std::vector<uint64_t> rshift_mag(const std::vector<uint64_t>& a, size_t bits) {
        if (a.empty() || bits == 0) {
            return a;
        }
        size_t word_shift = bits / 64;
        size_t bit_shift = bits % 64;
        if (word_shift >= a.size()) {
            return {};
        }

        std::vector<uint64_t> out(a.size() - word_shift, 0);
        if (bit_shift == 0) {
            for (size_t i = word_shift; i < a.size(); ++i) {
                out[i - word_shift] = a[i];
            }
            trim_mag(out);
            return out;
        }

        for (size_t i = word_shift; i < a.size(); ++i) {
            uint64_t low = a[i] >> bit_shift;
            uint64_t high = 0;
            if (i + 1 < a.size()) {
                high = a[i + 1] << (64 - bit_shift);
            }
            out[i - word_shift] = low | high;
        }
        trim_mag(out);
        return out;
    }

    static void rshift1_inplace(std::vector<uint64_t>& a) {
        uint64_t carry = 0;
        for (size_t i = a.size(); i > 0; --i) {
            size_t idx = i - 1;
            uint64_t new_carry = a[idx] << 63;
            a[idx] = (a[idx] >> 1) | carry;
            carry = new_carry;
        }
        trim_mag(a);
    }

    static size_t bit_length_mag(const std::vector<uint64_t>& a) {
        if (a.empty()) {
            return 0;
        }
        uint64_t ms = a.back();
        return (a.size() - 1) * 64 + (64 - static_cast<size_t>(__builtin_clzll(ms)));
    }

    static void set_bit(std::vector<uint64_t>& a, size_t bit_idx) {
        size_t limb_idx = bit_idx / 64;
        size_t off = bit_idx % 64;
        if (limb_idx >= a.size()) {
            a.resize(limb_idx + 1, 0);
        }
        a[limb_idx] |= (uint64_t(1) << off);
    }

    static uint32_t div_small_inplace(std::vector<uint64_t>& a, uint32_t divisor) {
        uint64_t rem = 0;
        for (size_t i = a.size(); i > 0; --i) {
            size_t idx = i - 1;
            unsigned __int128 cur = (static_cast<unsigned __int128>(rem) << 64) | a[idx];
            a[idx] = static_cast<uint64_t>(cur / divisor);
            rem = static_cast<uint64_t>(cur % divisor);
        }
        trim_mag(a);
        return static_cast<uint32_t>(rem);
    }

    static void divmod_mag(
        const std::vector<uint64_t>& num,
        const std::vector<uint64_t>& den,
        std::vector<uint64_t>& q,
        std::vector<uint64_t>& r
    ) {
        if (den.empty()) {
            tpy_panic("Division by zero");
        }
        if (num.empty()) {
            q.clear();
            r.clear();
            return;
        }

        int cmp = cmp_mag(num, den);
        if (cmp < 0) {
            q.clear();
            r = num;
            return;
        }
        if (cmp == 0) {
            q = {1};
            r.clear();
            return;
        }

        if (den.size() == 1) {
            uint64_t d = den[0];
            q.assign(num.size(), 0);
            uint64_t rem = 0;
            for (size_t i = num.size(); i > 0; --i) {
                size_t idx = i - 1;
                unsigned __int128 cur = (static_cast<unsigned __int128>(rem) << 64) | num[idx];
                q[idx] = static_cast<uint64_t>(cur / d);
                rem = static_cast<uint64_t>(cur % d);
            }
            trim_mag(q);
            r.clear();
            if (rem != 0) {
                r.push_back(rem);
            }
            return;
        }

        size_t nbits = bit_length_mag(num);
        size_t dbits = bit_length_mag(den);
        size_t shift = nbits - dbits;

        std::vector<uint64_t> d = lshift_mag(den, shift);
        r = num;
        q.clear();

        for (size_t i = shift + 1; i > 0; --i) {
            size_t bit = i - 1;
            if (cmp_mag(r, d) >= 0) {
                r = sub_mag(r, d);
                set_bit(q, bit);
            }
            rshift1_inplace(d);
        }

        trim_mag(q);
        trim_mag(r);
    }

    bool to_uint64_checked(uint64_t& out) const {
        if (is_small()) {
            int64_t v = small_value();
            if (v < 0) {
                return false;
            }
            out = static_cast<uint64_t>(v);
            return true;
        }
        const HeapBig* p = heap_ptr();
        if (p->sign < 0 || p->len > 1) {
            return false;
        }
        out = (p->len == 0) ? 0 : p->limbs[0];
        return true;
    }

    size_t abs_bit_length() const {
        if (is_small()) {
            uint64_t mag = abs_u64(small_value());
            if (mag == 0) return 0;
            return 64 - static_cast<size_t>(__builtin_clzll(mag));
        }
        const HeapBig* p = heap_ptr();
        if (p->len == 0) return 0;
        uint64_t ms = p->limbs[p->len - 1];
        return (static_cast<size_t>(p->len) - 1) * 64 +
               (64 - static_cast<size_t>(__builtin_clzll(ms)));
    }

    static std::vector<uint64_t> to_twos_complement(const BigInt& x, size_t limbs_n) {
        std::vector<uint64_t> out(limbs_n, 0);
        std::vector<uint64_t> mag = x.abs_limbs();
        for (size_t i = 0; i < mag.size() && i < limbs_n; ++i) {
            out[i] = mag[i];
        }

        if (x.signum() < 0) {
            for (size_t i = 0; i < out.size(); ++i) {
                out[i] = ~out[i];
            }
            uint64_t carry = 1;
            for (size_t i = 0; i < out.size() && carry != 0; ++i) {
                unsigned __int128 sum = static_cast<unsigned __int128>(out[i]) + carry;
                out[i] = static_cast<uint64_t>(sum);
                carry = static_cast<uint64_t>(sum >> 64);
            }
        }
        return out;
    }

    static BigInt from_twos_complement(std::vector<uint64_t> tc) {
        if (tc.empty()) {
            return BigInt(0);
        }
        bool negative = ((tc.back() >> 63) != 0);
        if (!negative) {
            trim_mag(tc);
            return from_sign_mag(1, std::move(tc));
        }

        for (size_t i = 0; i < tc.size(); ++i) {
            tc[i] = ~tc[i];
        }
        uint64_t carry = 1;
        for (size_t i = 0; i < tc.size() && carry != 0; ++i) {
            unsigned __int128 sum = static_cast<unsigned __int128>(tc[i]) + carry;
            tc[i] = static_cast<uint64_t>(sum);
            carry = static_cast<uint64_t>(sum >> 64);
        }
        trim_mag(tc);
        return from_sign_mag(-1, std::move(tc));
    }

    BigInt bitwise_binary(const BigInt& rhs, char op) const {
        size_t a_limbs = (abs_bit_length() + 63) / 64;
        size_t b_limbs = (rhs.abs_bit_length() + 63) / 64;
        size_t n = (a_limbs > b_limbs ? a_limbs : b_limbs) + 2;
        if (n == 0) n = 2;

        std::vector<uint64_t> a = to_twos_complement(*this, n);
        std::vector<uint64_t> b = to_twos_complement(rhs, n);
        std::vector<uint64_t> out(n, 0);

        for (size_t i = 0; i < n; ++i) {
            if (op == '&') {
                out[i] = a[i] & b[i];
            } else if (op == '|') {
                out[i] = a[i] | b[i];
            } else {
                out[i] = a[i] ^ b[i];
            }
        }
        return from_twos_complement(std::move(out));
    }

    int compare(const BigInt& rhs) const {
        int a_sign = signum();
        int b_sign = rhs.signum();
        if (a_sign != b_sign) {
            return (a_sign > b_sign) ? 1 : -1;
        }
        if (a_sign == 0) {
            return 0;
        }

        std::vector<uint64_t> a = abs_limbs();
        std::vector<uint64_t> b = rhs.abs_limbs();
        int cmp = cmp_mag(a, b);
        return (a_sign > 0) ? cmp : -cmp;
    }

    BigInt floor_div(const BigInt& rhs) const {
        if (rhs.signum() == 0) {
            tpy_panic("Division by zero");
        }
        if (signum() == 0) {
            return BigInt(0);
        }

        int a_sign = signum();
        int b_sign = rhs.signum();
        std::vector<uint64_t> a = abs_limbs();
        std::vector<uint64_t> b = rhs.abs_limbs();

        std::vector<uint64_t> q_abs;
        std::vector<uint64_t> r_abs;
        divmod_mag(a, b, q_abs, r_abs);

        bool rem_zero = r_abs.empty();
        if (a_sign == b_sign) {
            return from_sign_mag(1, std::move(q_abs));
        }

        if (rem_zero) {
            return from_sign_mag(-1, std::move(q_abs));
        }

        add_small_inplace(q_abs, 1);
        return from_sign_mag(-1, std::move(q_abs));
    }

    BigInt floor_mod(const BigInt& rhs) const {
        if (rhs.signum() == 0) {
            tpy_panic("Division by zero");
        }
        if (signum() == 0) {
            return BigInt(0);
        }

        int a_sign = signum();
        int b_sign = rhs.signum();
        std::vector<uint64_t> a = abs_limbs();
        std::vector<uint64_t> b = rhs.abs_limbs();

        std::vector<uint64_t> q_abs;
        std::vector<uint64_t> r_abs;
        divmod_mag(a, b, q_abs, r_abs);

        if (r_abs.empty()) {
            return BigInt(0);
        }

        if (a_sign == b_sign) {
            return from_sign_mag(b_sign, std::move(r_abs));
        }

        std::vector<uint64_t> adjusted = sub_mag(b, r_abs);  // |b| - r
        return from_sign_mag(b_sign, std::move(adjusted));
    }

    static std::string pad9(uint32_t v) {
        std::string s = std::to_string(v);
        if (s.size() >= 9) {
            return s;
        }
        return std::string(9 - s.size(), '0') + s;
    }
};

static_assert(sizeof(BigInt) == sizeof(uintptr_t), "BigInt must stay 8 bytes");

// Stream output operator for BigInt
inline std::ostream& operator<<(std::ostream& os, const BigInt& val) {
    return os << val.to_string();
}

} // namespace tpy
