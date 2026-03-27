/**
 * TurboPython Runtime - Builtin Function Helpers
 *
 * Runtime support for round(), divmod(), ord(), and related builtins.
 * Depends on: core.hpp, fixed_int.hpp, bigint.hpp
 */

#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <format>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

namespace tpy {

// -- ord/Char helpers --

// ord(s: str) -- panics if len(s) != 1, like Python's TypeError
inline int32_t ord_str(std::string_view s) {
    if (s.size() != 1) {
        tpy_panic("ord() expected a string of length 1");
    }
    return static_cast<int32_t>(static_cast<unsigned char>(s[0]));
}

// Char(s: str) -- extract single character, panics if len(s) != 1
inline char char_from_str(std::string_view s) {
    if (s.size() != 1) {
        tpy_panic("Char() expected a string of length 1");
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
            tpy_panic((std::string(fixed_int_name<T>()) + " overflow in round").c_str());
        }
        return res;
    } else if (rem == half) {
        T unit = base / factor;
        if (unit % 2 != 0) {
            T res;
            if (__builtin_add_overflow(base, factor, &res)) {
                tpy_panic((std::string(fixed_int_name<T>()) + " overflow in round").c_str());
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
    if (b == 0.0) tpy_panic("Division by zero");
    double q = std::floor(a / b);
    double r = a - q * b;
    return {q, r};
}

// divmod(fixed_int, fixed_int) -> tuple[T, T]  (Python floor division + mod)
template<typename T>
std::tuple<T, T> divmod_fixed(T a, T b) {
    if (b == 0) tpy_panic("Division by zero");
    if constexpr (std::is_signed_v<T>) {
        if (a == std::numeric_limits<T>::min() && b == static_cast<T>(-1)) {
            tpy_panic((std::string(fixed_int_name<T>()) + " overflow in division").c_str());
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

// -- all / any --

template<typename Iter>
bool builtin_all(Iter&& iterable) {
    for (auto&& elem : iterable) {
        if (!static_cast<bool>(elem)) return false;
    }
    return true;
}

template<typename Iter>
bool builtin_any(Iter&& iterable) {
    for (auto&& elem : iterable) {
        if (static_cast<bool>(elem)) return true;
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
    std::string result;
    if (negative) result += '-';
    result += "0x";
    result += std::format("{:x}", val);
    return result;
}

inline std::string builtin_oct(int64_t x) {
    if (x == 0) return "0o0";
    bool negative = x < 0;
    uint64_t val = negative ? -static_cast<uint64_t>(x) : static_cast<uint64_t>(x);
    std::string result;
    if (negative) result += '-';
    result += "0o";
    result += std::format("{:o}", val);
    return result;
}

} // namespace tpy
