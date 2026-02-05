/**
 * TurboPython Runtime Header
 *
 * Provides the core types and utilities for TurboPython compiled code:
 * - StaticList<T, N>: Fixed-capacity container
 * - tpy_panic(): Abort on fatal error
 */

#pragma once

#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cmath>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <iterator>
#include <algorithm>
#include <array>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>
#include <ranges>
#include <optional>
#include <chrono>
#include <thread>
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

/**
 * Helper struct for Python-style bool printing.
 *
 * Matches Python's repr for bools: prints "True" or "False".
 */
struct print_bool {
    bool value;
    explicit print_bool(bool v) : value(v) {}
};

inline std::ostream& operator<<(std::ostream& os, const print_bool& pb) {
    os << (pb.value ? "True" : "False");
    return os;
}

/**
 * Helper struct for float printing that matches Python's repr() behavior.
 *
 * - Whole numbers display with ".0" (e.g., "5.0" not "5")
 * - Large numbers use decimal notation for reasonable range
 * - Very small numbers (< 0.0001) use scientific notation like Python
 * - Uses shortest representation that round-trips correctly
 */
struct print_float {
    double value;
    explicit print_float(double v) : value(v) {}
};

inline std::string format_float(double value) {
    // Handle special cases
    if (std::isnan(value)) return "nan";
    if (std::isinf(value)) return value > 0 ? "inf" : "-inf";

    double abs_val = std::fabs(value);

    // Python uses scientific notation for very small or very large numbers
    // Threshold: |value| < 0.0001 or |value| >= 1e16
    bool use_scientific = (abs_val != 0.0 && abs_val < 0.0001) || abs_val >= 1e16;

    if (use_scientific) {
        // Use scientific notation - find shortest representation
        for (int prec = 1; prec <= 17; ++prec) {
            std::ostringstream oss;
            oss << std::scientific << std::setprecision(prec - 1) << value;
            std::string s = oss.str();

            // Normalize: remove leading zeros in exponent, use 'e' not 'e+'
            // C++ outputs "1.000000e+05", Python outputs "1e+05" or "1e-05"
            double parsed = std::stod(s);
            if (parsed == value) {
                // Simplify the representation to match Python
                // Find 'e' and process
                auto e_pos = s.find('e');
                if (e_pos != std::string::npos) {
                    std::string mantissa = s.substr(0, e_pos);
                    std::string exponent = s.substr(e_pos);

                    // Trim trailing zeros from mantissa (keep at least one digit after .)
                    auto dot = mantissa.find('.');
                    if (dot != std::string::npos) {
                        auto last_nonzero = mantissa.find_last_not_of('0');
                        if (last_nonzero != std::string::npos && last_nonzero > dot) {
                            mantissa = mantissa.substr(0, last_nonzero + 1);
                        } else {
                            mantissa = mantissa.substr(0, dot);  // Remove decimal entirely if just zeros
                        }
                    }

                    // Simplify exponent: e+05 -> e+05, e-05 -> e-05
                    // Remove leading zeros: e+05 -> e+5 (but Python keeps them... check)
                    s = mantissa + exponent;
                }
                return s;
            }
        }
    } else {
        // Use fixed notation - find shortest representation
        for (int prec = 1; prec <= 17; ++prec) {
            std::ostringstream oss;
            oss << std::fixed << std::setprecision(prec) << value;
            std::string s = oss.str();

            // Check if this representation round-trips
            double parsed = std::stod(s);
            if (parsed == value) {
                // Trim trailing zeros, but keep at least one digit after decimal
                auto dot = s.find('.');
                if (dot != std::string::npos) {
                    auto last_nonzero = s.find_last_not_of('0');
                    if (last_nonzero != std::string::npos && last_nonzero > dot) {
                        s = s.substr(0, last_nonzero + 1);
                    } else {
                        s = s.substr(0, dot + 2);
                    }
                }
                return s;
            }
        }
    }

    // Fallback: use default precision
    std::ostringstream oss;
    oss << value;
    return oss.str();
}

inline std::ostream& operator<<(std::ostream& os, const print_float& pf) {
    os << format_float(pf.value);
    return os;
}

template <typename T>
const T& deref_ptr(const T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

/**
 * char_to_str - Convert a char to a string_view.
 *
 * Returns a string_view pointing to a static single-character string.
 * Uses a lookup table to avoid allocation.
 */
inline std::string_view char_to_str(char c) {
    // Static array of single-character null-terminated strings
    static const char chars[256][2] = {
        "\x00", "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",
        "\x08", "\x09", "\x0a", "\x0b", "\x0c", "\x0d", "\x0e", "\x0f",
        "\x10", "\x11", "\x12", "\x13", "\x14", "\x15", "\x16", "\x17",
        "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d", "\x1e", "\x1f",
        " ", "!", "\"", "#", "$", "%", "&", "'", "(", ")", "*", "+", ",", "-", ".", "/",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ":", ";", "<", "=", ">", "?",
        "@", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O",
        "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "[", "\\", "]", "^", "_",
        "`", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o",
        "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z", "{", "|", "}", "~", "\x7f",
        "\x80", "\x81", "\x82", "\x83", "\x84", "\x85", "\x86", "\x87",
        "\x88", "\x89", "\x8a", "\x8b", "\x8c", "\x8d", "\x8e", "\x8f",
        "\x90", "\x91", "\x92", "\x93", "\x94", "\x95", "\x96", "\x97",
        "\x98", "\x99", "\x9a", "\x9b", "\x9c", "\x9d", "\x9e", "\x9f",
        "\xa0", "\xa1", "\xa2", "\xa3", "\xa4", "\xa5", "\xa6", "\xa7",
        "\xa8", "\xa9", "\xaa", "\xab", "\xac", "\xad", "\xae", "\xaf",
        "\xb0", "\xb1", "\xb2", "\xb3", "\xb4", "\xb5", "\xb6", "\xb7",
        "\xb8", "\xb9", "\xba", "\xbb", "\xbc", "\xbd", "\xbe", "\xbf",
        "\xc0", "\xc1", "\xc2", "\xc3", "\xc4", "\xc5", "\xc6", "\xc7",
        "\xc8", "\xc9", "\xca", "\xcb", "\xcc", "\xcd", "\xce", "\xcf",
        "\xd0", "\xd1", "\xd2", "\xd3", "\xd4", "\xd5", "\xd6", "\xd7",
        "\xd8", "\xd9", "\xda", "\xdb", "\xdc", "\xdd", "\xde", "\xdf",
        "\xe0", "\xe1", "\xe2", "\xe3", "\xe4", "\xe5", "\xe6", "\xe7",
        "\xe8", "\xe9", "\xea", "\xeb", "\xec", "\xed", "\xee", "\xef",
        "\xf0", "\xf1", "\xf2", "\xf3", "\xf4", "\xf5", "\xf6", "\xf7",
        "\xf8", "\xf9", "\xfa", "\xfb", "\xfc", "\xfd", "\xfe", "\xff"
    };
    return std::string_view(chars[static_cast<unsigned char>(c)], 1);
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
 * Return type helpers for generic code.
 *
 * These pick value or reference return types based on is_value_type trait:
 * - return_val_or_ref_t<T>: T for value types, T& for object types (mutable)
 * - return_val_or_cref_t<T>: T for value types, const T& for object types (const)
 */
template<typename T>
using return_val_or_ref_t = std::conditional_t<is_value_type<T>::value, T, T&>;

template<typename T>
using return_val_or_cref_t = std::conditional_t<is_value_type<T>::value, T, const T&>;

/**
 * Parameter type helper for generic code.
 *
 * Picks parameter type based on is_value_type trait:
 * - const T& for value types (immutable in Python, compiler optimizes small types)
 * - T& for object types (mutable in Python)
 */
template<typename T>
using param_val_or_ref_t = std::conditional_t<is_value_type<T>::value, const T&, T&>;

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
 * from_range<Container> - Construct a container from a range.
 *
 * Constructs container using iterator-pair constructor (begin, end).
 * If container supports reserve() and range has known size, reserves first.
 * Works with std::vector, StaticList, and any container with this constructor.
 *
 * Usage: tpy::from_range<StaticList<int, 10>>(some_range)
 *        tpy::from_range<std::vector<int>>(some_range)
 */
template<typename Container, std::ranges::input_range R>
Container from_range(R&& range) {
    if constexpr (requires(Container& c) { c.reserve(std::size_t{}); } &&
                  std::ranges::sized_range<R>) {
        Container result;
        result.reserve(std::ranges::size(range));
        result.assign(std::ranges::begin(range), std::ranges::end(range));
        return result;
    } else {
        return Container(std::ranges::begin(range), std::ranges::end(range));
    }
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

    // Iterator-pair constructor
    template<std::input_iterator InputIt>
        requires std::convertible_to<std::iter_value_t<InputIt>, T>
    StaticList(InputIt first, InputIt last) : size_(0) {
        for (; first != last; ++first) {
            if (size_ >= N) {
                tpy_panic("StaticList capacity exceeded");
            }
            data_[size_++] = *first;
        }
    }

    // Span constructor
    StaticList(std::span<const T> items) : size_(0) {
        if (items.size() > N) {
            tpy_panic("StaticList capacity exceeded");
        }
        for (const auto& val : items) {
            data_[size_++] = val;
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

/**
 * bool_to_str - Convert bool to "True" or "False" string.
 * Returns const char* pointing to static storage (safe for std::string_view).
 */
inline const char* bool_to_str(bool x) {
    return x ? "True" : "False";
}

/**
 * int32_to_str - Convert Int32 to string.
 * Note: Returns std::string. Caller must ensure the result is used immediately
 * or stored in std::string/auto, not std::string_view.
 */
inline std::string int32_to_str(int32_t x) {
    return std::to_string(x);
}

/**
 * float_to_str - Convert double to string.
 * Produces Python-like output (removes trailing zeros after decimal point).
 * Note: Returns std::string. Caller must ensure the result is used immediately
 * or stored in std::string/auto, not std::string_view.
 */
inline std::string float_to_str(double x) {
    // Use Python's repr-like approach: shortest representation that round-trips
    std::ostringstream oss;
    oss << std::setprecision(15) << x;
    std::string result = oss.str();

    // If no decimal point and no exponent, add .0 for Python compatibility
    if (result.find('.') == std::string::npos && result.find('e') == std::string::npos) {
        result += ".0";
    }

    // Remove trailing zeros after decimal point (but keep at least one digit)
    size_t dot = result.find('.');
    if (dot != std::string::npos) {
        size_t e_pos = result.find('e');
        size_t end = (e_pos != std::string::npos) ? e_pos : result.size();
        while (end > dot + 2 && result[end - 1] == '0') {
            --end;
        }
        if (e_pos != std::string::npos) {
            result = result.substr(0, end) + result.substr(e_pos);
        } else {
            result = result.substr(0, end);
        }
    }
    return result;
}

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
 * get_char - Bounds-checked character access for strings.
 *
 * Returns character at index. Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
inline char get_char(std::string_view s, int32_t index) {
    auto i = normalize_index(s, index, "string index out of bounds");
    return s[i];
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

/**
 * list_pop_at - Python list.pop(index) for std::vector.
 *
 * Removes and returns element at index. Supports negative indexing.
 * Panics if index is out of bounds.
 */
template<typename T>
T list_pop_at(std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "pop index out of range");
    T result = std::move(v[i]);
    v.erase(v.begin() + i);
    return result;
}

/**
 * list_index - Python list.index(value) for std::vector.
 *
 * Returns index of first occurrence of value. Panics if not found.
 */
template<typename T>
int32_t list_index(const std::vector<T>& v, const T& value) {
    auto it = std::find(v.begin(), v.end(), value);
    if (it == v.end()) {
        tpy_panic("list.index(x): x not in list");
    }
    return static_cast<int32_t>(it - v.begin());
}

/**
 * list_count - Python list.count(value) for std::vector.
 *
 * Returns number of occurrences of value.
 */
template<typename T>
int32_t list_count(const std::vector<T>& v, const T& value) {
    return static_cast<int32_t>(std::count(v.begin(), v.end(), value));
}

/**
 * list_reverse - Python list.reverse() for std::vector.
 *
 * Reverses the list in place.
 */
template<typename T>
void list_reverse(std::vector<T>& v) {
    std::reverse(v.begin(), v.end());
}

/**
 * list_copy - Python list.copy() for std::vector.
 *
 * Returns a shallow copy of the list.
 */
template<typename T>
std::vector<T> list_copy(const std::vector<T>& v) {
    return v;
}

// =============================================
// StaticList helper functions
// =============================================

/**
 * staticlist_extend - Python list.extend() for StaticList.
 *
 * Extends StaticList with elements from another container.
 * Panics if capacity would be exceeded.
 */
template<typename T, std::size_t N, typename Container>
void staticlist_extend(StaticList<T, N>& sl, const Container& other) {
    for (const auto& elem : other) {
        sl.push_back(elem);
    }
}

template<typename T, std::size_t N>
void staticlist_extend(StaticList<T, N>& sl, std::initializer_list<T> other) {
    for (const auto& elem : other) {
        sl.push_back(elem);
    }
}

/**
 * staticlist_insert - Python list.insert() for StaticList.
 *
 * Inserts value at index. Supports negative indexing and clamps to valid range.
 * Panics if capacity would be exceeded.
 * Does not require T to be default-constructible.
 */
template<typename T, std::size_t N, typename V>
void staticlist_insert(StaticList<T, N>& sl, int32_t index, V&& value) {
    std::ptrdiff_t i = index;
    auto sz = static_cast<std::ptrdiff_t>(sl.size());
    if (i < 0) {
        i += sz;
        if (i < 0) i = 0;
    } else if (i > sz) {
        i = sz;
    }
    // Inserting at end is just push_back
    if (i == sz) {
        sl.push_back(std::forward<V>(value));
        return;
    }
    // Store value, extend by copying last element, shift, then place value
    T temp(std::forward<V>(value));
    sl.push_back(std::move(sl[sz - 1]));
    for (std::ptrdiff_t j = sz - 1; j > i; --j) {
        sl[j] = std::move(sl[j - 1]);
    }
    sl[i] = std::move(temp);
}

/**
 * staticlist_remove - Python list.remove() for StaticList.
 *
 * Removes first occurrence of value. Panics if not found.
 */
template<typename T, std::size_t N>
void staticlist_remove(StaticList<T, N>& sl, const T& value) {
    auto it = std::find(sl.begin(), sl.end(), value);
    if (it == sl.end()) {
        tpy_panic("list.remove(x): x not in list");
    }
    // Shift elements left
    for (auto p = it; p + 1 != sl.end(); ++p) {
        *p = std::move(*(p + 1));
    }
    sl.pop_back();  // Decrease size (discards return value)
}

/**
 * staticlist_pop_at - Python list.pop(index) for StaticList.
 *
 * Removes and returns element at index. Supports negative indexing.
 * Panics if index is out of bounds.
 */
template<typename T, std::size_t N>
T staticlist_pop_at(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "pop index out of range");
    T result = std::move(sl[i]);
    // Shift elements left
    for (std::size_t j = i; j + 1 < static_cast<std::size_t>(sl.size()); ++j) {
        sl[j] = std::move(sl[j + 1]);
    }
    sl.pop_back();  // Decrease size (discards return value)
    return result;
}

/**
 * staticlist_index - Python list.index(value) for StaticList.
 *
 * Returns index of first occurrence of value. Panics if not found.
 */
template<typename T, std::size_t N>
int32_t staticlist_index(const StaticList<T, N>& sl, const T& value) {
    auto it = std::find(sl.begin(), sl.end(), value);
    if (it == sl.end()) {
        tpy_panic("list.index(x): x not in list");
    }
    return static_cast<int32_t>(it - sl.begin());
}

/**
 * staticlist_count - Python list.count(value) for StaticList.
 *
 * Returns number of occurrences of value.
 */
template<typename T, std::size_t N>
int32_t staticlist_count(const StaticList<T, N>& sl, const T& value) {
    return static_cast<int32_t>(std::count(sl.begin(), sl.end(), value));
}

/**
 * staticlist_reverse - Python list.reverse() for StaticList.
 *
 * Reverses the list in place.
 */
template<typename T, std::size_t N>
void staticlist_reverse(StaticList<T, N>& sl) {
    std::reverse(sl.begin(), sl.end());
}

// =============================================
// Protocol free functions - unified interface for dunder methods
// =============================================

/**
 * tpy::__len__ - Protocol-based length accessor
 *
 * Enables len() to work uniformly across all container types:
 * - User types: calls x.__len__() method
 * - std types: overloads call .size()
 *
 * This allows functions with protocol-typed parameters to work with
 * both user-defined and standard library types.
 */

// Overload: std::vector (most specific, checked first)
template<typename T>
int32_t __len__(const std::vector<T>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::array
template<typename T, std::size_t N>
int32_t __len__(const std::array<T, N>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::span (const and non-const)
template<typename T>
int32_t __len__(std::span<const T> x) {
    return static_cast<int32_t>(x.size());
}

template<typename T>
int32_t __len__(std::span<T> x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::string_view
inline int32_t __len__(std::string_view x) {
    return static_cast<int32_t>(x.size());
}

// Overload: const char* (string literals)
inline int32_t __len__(const char* x) {
    return static_cast<int32_t>(std::string_view(x).size());
}

// Overload: StaticList
template<typename T, std::size_t N>
int32_t __len__(const StaticList<T, N>& x) {
    return x.size();  // StaticList::size() already returns int32_t
}

// Default template: user types that define __len__() method
// This is checked last due to the requires clause
template<typename T>
    requires requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; }
int32_t __len__(const T& x) {
    return x.__len__();
}

/**
 * Sized concept - types that support tpy::__len__()
 *
 * Matches Python's typing.Protocol approach to structural subtyping.
 * A type is Sized if tpy::__len__(x) is valid and returns int32_t.
 */
template<typename T>
concept Sized = requires(const T& t) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};

/**
 * Sequence concept - types that support tpy::__len__() and indexing
 *
 * Generic protocol parameterized by element type ElemT.
 * A type is Sequence<ElemT> if it has len() and operator[](int32_t) -> ElemT.
 */
template<typename T, typename ElemT>
concept Sequence = requires(const T& t, int32_t i) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
    { t[i] } -> std::convertible_to<ElemT>;
};

/**
 * NativeIterable concept - types that support C++ range-based for loops
 *
 * A type is NativeIterable<ElemT> if it supports begin()/end() iteration
 * and dereferencing yields ElemT. This is the "native" C++ iteration pattern.
 *
 * Future: Iterable<T> will use Python's __iter__/__next__ protocol.
 */
template<typename T, typename ElemT>
concept NativeIterable = requires(const T& t) {
    { std::ranges::begin(t) } -> std::input_or_output_iterator;
    { std::ranges::end(t) } -> std::sentinel_for<decltype(std::ranges::begin(t))>;
    { *std::ranges::begin(t) } -> std::convertible_to<ElemT>;
};

/**
 * NativeContiguous concept - types with elements laid out contiguously in memory
 *
 * A type is NativeContiguous<ElemT> if it's a contiguous_range with elements
 * convertible to ElemT. Types conforming to NativeContiguous can be implicitly
 * converted to std::span.
 */
template<typename T, typename ElemT>
concept NativeContiguous = std::ranges::contiguous_range<T> &&
    std::convertible_to<std::ranges::range_reference_t<T>, ElemT>;

/**
 * MutableSequence concept - types that support len(), read indexing, and write indexing
 *
 * A type is MutableSequence<ElemT> if it satisfies Sequence<ElemT> and additionally
 * supports assignment via subscript operator (t[i] = v).
 */
template<typename T, typename ElemT>
concept MutableSequence = Sequence<T, ElemT> && requires(T& t, int32_t i, ElemT v) {
    { t[i] = v };
};

/**
 * NativeRangeConstructible concept - types that can be constructed from a range
 *
 * A type is NativeRangeConstructible<ElemT> if from_range<T> can construct it.
 * This requires an iterator-pair constructor (begin, end).
 */
template<typename T, typename ElemT>
concept NativeRangeConstructible = requires(repeat_range<ElemT> r) {
    T(std::ranges::begin(r), std::ranges::end(r));
};

/**
 * Comparable concept - types that support the < operator
 *
 * A type is Comparable if it supports t < t comparison returning bool.
 * This is used for bounded type parameters like T: Comparable.
 */
template<typename T>
concept Comparable = requires(const T& a, const T& b) {
    { a < b } -> std::convertible_to<bool>;
};

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

// --- Generic value printing for type parameters ---

/**
 * ValuePrinter<T> - Prints values handling both scalars and containers.
 *
 * Used for generic type parameters where T might be a value type (int32_t)
 * or a container type (std::vector). Uses ListPrinter for ranges.
 */
template<typename T>
struct ValuePrinter {
    const T& value;
    explicit ValuePrinter(const T& v) : value(v) {}
};

template<typename T>
std::ostream& operator<<(std::ostream& os, const ValuePrinter<T>& p) {
    // String types are ranges but should print as strings, not char lists
    if constexpr (std::is_same_v<T, std::string_view> ||
                  std::is_same_v<T, std::string> ||
                  std::is_same_v<T, const char*>) {
        return os << p.value;
    } else if constexpr (std::ranges::range<T>) {
        return os << ListPrinter(p.value);
    } else {
        return os << p.value;
    }
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
 * time_time - Return seconds since epoch as double.
 *
 * Equivalent to Python's time.time().
 */
inline double time_time() {
    auto now = std::chrono::system_clock::now();
    auto duration = now.time_since_epoch();
    return std::chrono::duration<double>(duration).count();
}

/**
 * time_sleep - Suspend execution for the given number of seconds.
 *
 * Equivalent to Python's time.sleep().
 */
inline void time_sleep(double seconds) {
    if (seconds < 0) {
        tpy_panic("sleep length must be non-negative");
    }
    auto duration = std::chrono::duration<double>(seconds);
    std::this_thread::sleep_for(duration);
}

/**
 * sys_argv - Command line arguments as vector of string_view.
 *
 * Equivalent to Python's sys.argv. Initialized by init_sys_argv() in main().
 * Note: string_views point to argv strings which are valid for program lifetime.
 */
inline std::vector<std::string_view> sys_argv;

/**
 * init_sys_argv - Initialize sys_argv from main()'s argc/argv.
 *
 * Called at program startup before __tpy_init().
 */
inline void init_sys_argv(int argc, char* argv[]) {
    sys_argv.clear();
    sys_argv.reserve(static_cast<std::size_t>(argc));
    for (int i = 0; i < argc; ++i) {
        sys_argv.emplace_back(argv[i]);
    }
}

} // namespace tpy

// Expose types in global namespace for TurboPython generated code
using tpy::StaticList;
using tpy::tpy_panic;
using tpy::BigInt;
