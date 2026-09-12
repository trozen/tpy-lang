/**
 * TurboPython Runtime - Builtin Function Helpers
 *
 * Runtime support for round(), divmod(), ord(), and related builtins.
 * Depends on: core.hpp, fixed_int.hpp, bigint.hpp
 */

#pragma once

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <format>
#include <iostream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

namespace tpy {

// -- stdin helper --

// Read a line from stdin (newline stripped). Raises EOFError on EOF,
// like CPython's `input()`. Powers the `input()` builtin.
inline std::string input_line() {
    std::string line;
    if (!std::getline(std::cin, line)) {
        raise_eof_error("EOF when reading a line");
    }
    return line;
}

// input(prompt): CPython writes the prompt to stdout with no trailing
// newline and flushes it, so the prompt is visible before the read blocks.
inline std::string input_line(std::string_view prompt) {
    std::cout << prompt << std::flush;
    return input_line();
}

// -- ord/char helpers --

// ord(s: str) -- throws TypeError if len(s) != 1, matching CPython.
inline int32_t ord_str(std::string_view s) {
    if (s.size() != 1) {
        raise_type_error("ord() expected a character, but string of length {} found",
                         s.size());
    }
    return static_cast<int32_t>(static_cast<unsigned char>(s[0]));
}

// char(s: str) -- extract single character, throws TypeError if len(s) != 1.
inline char char_from_str(std::string_view s) {
    if (s.size() != 1) {
        raise_type_error("char() expected a character, but string of length {} found",
                         s.size());
    }
    return s[0];
}

// -- round helpers --

// round[T](float) -> T  (banker's rounding = round half to even, matches Python)
template<typename T>
T round_to(double x) {
    double r = std::nearbyint(x);
    if constexpr (std::is_same_v<T, BigInt>) {
        return BigInt::from_float(r);
    } else {
        return static_cast<T>(r);
    }
}

// round(float, ndigits) -> float
inline double round_float(double x, int32_t ndigits) {
    if (ndigits >= 308) return x;
    if (ndigits <= -308) return 0.0;
    double factor = std::pow(10.0, static_cast<double>(ndigits));
    return std::nearbyint(x * factor) / factor;
}

// round(fixed_int, ndigits) -> fixed_int
template<typename T>
T round_fixed(T x, int32_t ndigits) {
    if (ndigits >= 0) return x;
    int32_t digits = -ndigits;
    T factor = 1;
    for (int32_t i = 0; i < digits; ++i) {
        T next;
        if (__builtin_mul_overflow(factor, static_cast<T>(10), &next)) {
            return 0;
        }
        factor = next;
    }
    T half = factor / 2;
    T rem = x % factor;
    if (rem < 0) rem += factor;
    T base = x - rem;
    // Banker's rounding: round half to even
    if (rem > half) {
        T res;
        if (__builtin_add_overflow(base, factor, &res)) {
            raise_fixedint_overflow("{} overflow in round", fixed_int_name<T>());
        }
        return res;
    } else if (rem == half) {
        T unit = base / factor;
        if (unit % 2 != 0) {
            T res;
            if (__builtin_add_overflow(base, factor, &res)) {
                raise_fixedint_overflow("{} overflow in round", fixed_int_name<T>());
            }
            return res;
        }
        return base;
    }
    return base;
}

// round(BigInt, ndigits) -> BigInt
inline BigInt round_bigint(const BigInt& x, int32_t ndigits) {
    if (ndigits >= 0) return x;
    int32_t digits = -ndigits;
    BigInt factor(1);
    for (int32_t i = 0; i < digits; ++i) {
        factor = factor * BigInt(10);
    }
    BigInt half = factor / BigInt(2);
    // BigInt::operator% uses floor_mod: rem is always in [0, factor)
    BigInt rem = x % factor;
    BigInt base = x - rem;
    if (rem > half) {
        return base + factor;
    } else if (rem == half) {
        BigInt unit = base / factor;
        if (unit % BigInt(2) != BigInt(0)) {
            return base + factor;
        }
        return base;
    }
    return base;
}

// -- divmod helpers --

// divmod(float, float) -> tuple[float, float]  (Python semantics)
inline std::tuple<double, double> divmod_float(double a, double b) {
    if (b == 0.0) raise_zero_division_error("float divmod()");
    double q = std::floor(a / b);
    double r = a - q * b;
    return {q, r};
}

// divmod(fixed_int, fixed_int) -> tuple[T, T]  (Python floor division + mod)
template<typename T>
std::tuple<T, T> divmod_fixed(T a, T b) {
    if (b == 0) raise_zero_division_error("integer division or modulo by zero");
    if constexpr (std::is_signed_v<T>) {
        if (a == std::numeric_limits<T>::min() && b == static_cast<T>(-1)) {
            raise_fixedint_overflow("{} overflow in division", fixed_int_name<T>());
        }
        T q = a / b;
        T r = a % b;
        if (r != 0 && ((r < 0) != (b < 0))) { q -= 1; r += b; }
        return {q, r};
    } else {
        return {a / b, a % b};
    }
}

// divmod(BigInt, BigInt) -> tuple[BigInt, BigInt]
inline std::tuple<BigInt, BigInt> divmod_bigint(const BigInt& a, const BigInt& b) {
    return a.floor_divmod(b);
}

// 3-arg min/max: returns const& to avoid copies for expensive types (BigInt)
template<typename T>
const T& min3(const T& a, const T& b, const T& c) {
    const auto& m = a < b ? a : b;
    return m < c ? m : c;
}

template<typename T>
const T& max3(const T& a, const T& b, const T& c) {
    const auto& m = a > b ? a : b;
    return m > c ? m : c;
}

// -- truthiness helper --

// Priority: implicit/explicit bool conversion > .empty() > __bool__() method.
// Primitives (int, bool, float, BigInt) match branch 1.
// std::string/string_view match branch 2.
// User records with __bool__() match branch 3.
template<typename T>
bool to_bool(const T& x) {
    if constexpr (std::is_same_v<T, std::monostate>) {
        // None is falsy in Python; std::monostate (TPy unit type at
        // value-bearing positions) has no bool conversion of its own.
        return false;
    } else if constexpr (std::is_constructible_v<bool, T>) {
        return static_cast<bool>(x);
    } else if constexpr (requires { x.empty(); }) {
        return !x.empty();
    } else if constexpr (requires { x.__bool__(); }) {
        return x.__bool__();
    } else {
        static_assert(false, "to_bool: type has no bool conversion, empty(), or __bool__()");
    }
}

// -- all / any --

template<typename Iter>
bool builtin_all(Iter&& iterable) {
    for (auto&& elem : iterable) {
        if (!to_bool(elem)) return false;
    }
    return true;
}

template<typename Iter>
bool builtin_any(Iter&& iterable) {
    for (auto&& elem : iterable) {
        if (to_bool(elem)) return true;
    }
    return false;
}

// -- sum --

template<typename T, typename Iter>
T builtin_sum(Iter&& iterable) {
    T result{};
    for (auto&& elem : iterable) {
        result = add_check<T>(result, static_cast<T>(elem));
    }
    return result;
}

template<typename T, typename Iter>
T builtin_sum_start(Iter&& iterable, T start) {
    for (auto&& elem : iterable) {
        start = add_check<T>(start, static_cast<T>(elem));
    }
    return start;
}

// BigInt sum (no overflow checks needed)
template<typename Iter>
BigInt builtin_sum_bigint(Iter&& iterable) {
    BigInt result(0);
    for (auto&& elem : iterable) result = result + elem;
    return result;
}

template<typename Iter>
BigInt builtin_sum_start_bigint(Iter&& iterable, const BigInt& start) {
    BigInt result = start;
    for (auto&& elem : iterable) result = result + elem;
    return result;
}

// float sum (no overflow checks)
template<typename Iter>
double builtin_sum_float(Iter&& iterable) {
    double result = 0.0;
    for (auto&& elem : iterable) result += static_cast<double>(elem);
    return result;
}

template<typename Iter>
double builtin_sum_start_float(Iter&& iterable, double start) {
    for (auto&& elem : iterable) start += static_cast<double>(elem);
    return start;
}

// -- sorted --

template<typename T, typename Iter>
std::vector<T> builtin_sorted(Iter&& iterable) {
    std::vector<T> result;
    for (auto&& elem : iterable) {
        result.emplace_back(std::forward<decltype(elem)>(elem));
    }
    std::stable_sort(result.begin(), result.end());
    return result;
}

template<typename T, typename Iter, typename KeyFn>
std::vector<T> builtin_sorted_key(Iter&& iterable, KeyFn&& key) {
    std::vector<T> items;
    for (auto&& elem : iterable) {
        items.emplace_back(std::forward<decltype(elem)>(elem));
    }
    // Schwartzian transform: compute key once per element (O(n)), then sort
    // by pre-computed keys (O(n log n) comparisons on cheap key values).
    using K = std::decay_t<decltype(key(items[0]))>;
    std::vector<std::pair<K, size_t>> decorated;
    decorated.reserve(items.size());
    for (size_t i = 0; i < items.size(); ++i) {
        decorated.emplace_back(key(items[i]), i);
    }
    std::stable_sort(decorated.begin(), decorated.end(),
        [](const auto& a, const auto& b) { return a.first < b.first; });
    std::vector<T> result;
    result.reserve(items.size());
    for (auto& [k, idx] : decorated) {
        result.emplace_back(std::move(items[idx]));
    }
    return result;
}

// -- min/max with key --

template<typename T, typename KeyFn>
const T& min_key(const T& a, const T& b, KeyFn&& key) {
    auto ka = key(a), kb = key(b);
    return kb < ka ? b : a;
}

template<typename T, typename KeyFn>
const T& min3_key(const T& a, const T& b, const T& c, KeyFn&& key) {
    auto ka = key(a), kb = key(b), kc = key(c);
    if (kb < ka) {
        return kc < kb ? c : b;
    }
    return kc < ka ? c : a;
}

template<typename T, typename KeyFn>
const T& max_key(const T& a, const T& b, KeyFn&& key) {
    auto ka = key(a), kb = key(b);
    return ka < kb ? b : a;
}

template<typename T, typename KeyFn>
const T& max3_key(const T& a, const T& b, const T& c, KeyFn&& key) {
    auto ka = key(a), kb = key(b), kc = key(c);
    if (ka < kb) {
        return kb < kc ? c : b;
    }
    return ka < kc ? c : a;
}

// -- bin / hex / oct --

inline std::string builtin_bin(int64_t x) {
    if (x == 0) return "0b0";
    std::string digits;
    bool negative = x < 0;
    uint64_t val = negative ? -static_cast<uint64_t>(x) : static_cast<uint64_t>(x);
    while (val > 0) {
        digits.push_back('0' + static_cast<char>(val & 1));
        val >>= 1;
    }
    std::string result;
    if (negative) result += '-';
    result += "0b";
    for (auto it = digits.rbegin(); it != digits.rend(); ++it) result += *it;
    return result;
}

inline std::string builtin_hex(int64_t x) {
    if (x == 0) return "0x0";
    bool negative = x < 0;
    uint64_t val = negative ? -static_cast<uint64_t>(x) : static_cast<uint64_t>(x);
    char buf[16];
    auto [ptr, ec] = std::to_chars(buf, buf + sizeof(buf), val, 16);
    std::string result;
    if (negative) result += '-';
    result += "0x";
    result.append(buf, ptr);
    return result;
}

inline std::string builtin_oct(int64_t x) {
    if (x == 0) return "0o0";
    bool negative = x < 0;
    uint64_t val = negative ? -static_cast<uint64_t>(x) : static_cast<uint64_t>(x);
    char buf[22];
    auto [ptr, ec] = std::to_chars(buf, buf + sizeof(buf), val, 8);
    std::string result;
    if (negative) result += '-';
    result += "0o";
    result.append(buf, ptr);
    return result;
}

// -- bin / hex / oct for BigInt --

inline std::string builtin_bin_bigint(const BigInt& x) {
    return x.to_bin_string();
}

inline std::string builtin_hex_bigint(const BigInt& x) {
    return x.to_hex_string();
}

inline std::string builtin_oct_bigint(const BigInt& x) {
    return x.to_oct_string();
}

} // namespace tpy
