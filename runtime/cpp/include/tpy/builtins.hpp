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
#include <optional>
#include <ranges>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

#include "next_iter.hpp"

namespace tpy {

// -- stdin helper --

// Read a line from stdin (newline stripped). Raises EOFError on EOF,
// like CPython's `input()`. Powers the `input()` builtin. With the SIGINT
// layer armed the read goes through it, so a Ctrl-C while waiting for input
// raises KeyboardInterrupt and, as in CPython, discards the part of the line
// typed before it.
inline std::string input_line() {
    std::string line;
#ifndef TPY_NO_SIGNALS
    if (const auto* ops = interrupt_detail::ops.load(std::memory_order_acquire)) {
        // std::getline would flush the tied std::cout first; keep doing so,
        // or a prompt printed without input()'s own prompt stays buffered.
        if (std::ostream* tied = std::cin.tie()) {
            tied->flush();
        }
        int r = ops->read_line(line);
        if (r == interrupt_detail::kInterrupted) {
            throw KeyboardInterrupt();
        }
        if (r == 0) {
            raise_eof_error("EOF when reading a line");
        }
        return line;
    }
#endif
    if (!std::getline(std::cin, line)) {
        raise_eof_error("EOF when reading a line");
    }
    return line;
}

// input(prompt): CPython writes the prompt to stdout with no trailing
// newline and flushes it, so the prompt is visible before the read blocks.
// A Ctrl-C already pending is taken first: CPython raises it before input()
// runs, so no prompt appears.
inline std::string input_line(std::string_view prompt) {
    check_signals();
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
    for (auto&& elem : ::tpy::iter_range(iterable)) {
        if (!to_bool(elem)) return false;
    }
    return true;
}

template<typename Iter>
bool builtin_any(Iter&& iterable) {
    for (auto&& elem : ::tpy::iter_range(iterable)) {
        if (to_bool(elem)) return true;
    }
    return false;
}

// -- sum --

template<typename T, typename Iter>
T builtin_sum(Iter&& iterable) {
    T result{};
    for (auto&& elem : ::tpy::iter_range(iterable)) {
        result = add_check<T>(result, static_cast<T>(elem));
    }
    return result;
}

template<typename T, typename Iter>
T builtin_sum_start(Iter&& iterable, T start) {
    for (auto&& elem : ::tpy::iter_range(iterable)) {
        start = add_check<T>(start, static_cast<T>(elem));
    }
    return start;
}

// BigInt sum (no overflow checks needed)
template<typename Iter>
BigInt builtin_sum_bigint(Iter&& iterable) {
    BigInt result(0);
    for (auto&& elem : ::tpy::iter_range(iterable)) result = result + elem;
    return result;
}

template<typename Iter>
BigInt builtin_sum_start_bigint(Iter&& iterable, const BigInt& start) {
    BigInt result = start;
    for (auto&& elem : ::tpy::iter_range(iterable)) result = result + elem;
    return result;
}

// float sum (no overflow checks)
template<typename Iter>
double builtin_sum_float(Iter&& iterable) {
    double result = 0.0;
    for (auto&& elem : ::tpy::iter_range(iterable)) result += static_cast<double>(elem);
    return result;
}

template<typename Iter>
double builtin_sum_start_float(Iter&& iterable, double start) {
    for (auto&& elem : ::tpy::iter_range(iterable)) start += static_cast<double>(elem);
    return start;
}

// -- sorted --

// `reverse` keeps equal elements in their original order, as CPython's
// reverse=True does: a stable sort under the flipped comparison.
template<typename T, typename Iter>
std::vector<T> builtin_sorted(Iter&& iterable, bool reverse = false) {
    std::vector<T> result;
    for (auto&& elem : ::tpy::iter_range(iterable)) {
        result.emplace_back(std::forward<decltype(elem)>(elem));
    }
    if (reverse) {
        std::stable_sort(result.begin(), result.end(),
            [](const T& a, const T& b) { return b < a; });
    } else {
        std::stable_sort(result.begin(), result.end());
    }
    return result;
}

template<typename T, typename Iter, typename KeyFn>
std::vector<T> builtin_sorted_key(Iter&& iterable, KeyFn&& key, bool reverse = false) {
    std::vector<T> items;
    for (auto&& elem : ::tpy::iter_range(iterable)) {
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
        [reverse](const auto& a, const auto& b) {
            return reverse ? b.first < a.first : a.first < b.first;
        });
    std::vector<T> result;
    result.reserve(items.size());
    for (auto& [k, idx] : decorated) {
        result.emplace_back(std::move(items[idx]));
    }
    return result;
}

// -- min/max over an iterable --

// A source whose elements stay put for the whole call: a forward range that
// hands out real references to T (a list, an array, a set, a dict's keys,
// named or temporary). An input range is not one: an iterator may hand out a
// reference to a slot it overwrites on the next step.
template<typename Iter, typename T>
concept stable_elements =
    std::ranges::forward_range<std::remove_reference_t<Iter>>
    && std::is_lvalue_reference_v<
        std::ranges::range_reference_t<std::remove_reference_t<Iter>>>
    && std::same_as<
        std::remove_cvref_t<
            std::ranges::range_reference_t<std::remove_reference_t<Iter>>>, T>;

// The first of equal elements wins, as in CPython.
template<typename T, typename Iter, typename Better>
T builtin_extreme(Iter&& iterable, std::string_view name, Better&& better) {
    if constexpr (stable_elements<Iter, T>) {
        const T* best = nullptr;
        for (const T& elem : iterable) {
            if (best == nullptr || better(elem, *best)) best = &elem;
        }
        if (best == nullptr) raise_value_error("{}() iterable argument is empty", name);
        return *best;
    } else {
        std::optional<T> best;
        for (auto&& elem : ::tpy::iter_range(iterable)) {
            const T& cur = elem;
            if (!best) best.emplace(cur);
            else if (better(cur, *best)) *best = cur;
        }
        if (!best) raise_value_error("{}() iterable argument is empty", name);
        return std::move(*best);
    }
}

template<typename T, typename Iter>
T builtin_min(Iter&& iterable) {
    return builtin_extreme<T>(std::forward<Iter>(iterable), "min",
        [](const T& a, const T& b) { return a < b; });
}

template<typename T, typename Iter>
T builtin_max(Iter&& iterable) {
    return builtin_extreme<T>(std::forward<Iter>(iterable), "max",
        [](const T& a, const T& b) { return b < a; });
}

// The key is computed once per element, and may be a view into the element
// it was computed from -- so it is always taken from an element that outlives
// the step: the container's own, or a copy held here (two slots, each
// candidate built in the one the winner does not occupy). The winner itself
// is always kept by copy: the key is user code, and one that writes the
// source must not change an element already chosen.
template<typename T, typename Iter, typename KeyFn, typename Better>
T builtin_extreme_key(Iter&& iterable, KeyFn&& key, std::string_view name,
                      Better&& better) {
    using K = std::decay_t<decltype(key(std::declval<const T&>()))>;
    std::optional<K> best_key;
    if constexpr (stable_elements<Iter, T>
                  && std::ranges::random_access_range<std::remove_reference_t<Iter>>
                  && std::ranges::sized_range<std::remove_reference_t<Iter>>) {
        // By index, re-reading the size and the element after the key ran:
        // a key that appends to the source is walked as Python walks a list,
        // the new elements included, with no iterator to invalidate.
        std::optional<T> best;
        using D = std::ranges::range_difference_t<std::remove_reference_t<Iter>>;
        for (D i = 0; i < static_cast<D>(std::ranges::size(iterable)); ++i) {
            K k = key(std::ranges::begin(iterable)[i]);
            if (!best || better(k, *best_key)) {
                // No named reference to the element: GCC 13's
                // -Wdangling-reference takes the iterator temporary for its referent.
                if (best) *best = std::ranges::begin(iterable)[i];
                else best.emplace(std::ranges::begin(iterable)[i]);
                best_key.emplace(std::move(k));
            }
        }
        if (!best) raise_value_error("{}() iterable argument is empty", name);
        return std::move(*best);
    } else if constexpr (stable_elements<Iter, T>) {
        std::optional<T> best;
        for (const T& elem : iterable) {
            K k = key(elem);
            if (!best || better(k, *best_key)) {
                if (best) *best = elem;
                else best.emplace(elem);
                best_key.emplace(std::move(k));
            }
        }
        if (!best) raise_value_error("{}() iterable argument is empty", name);
        return std::move(*best);
    } else {
        std::optional<T> slots[2];
        int best = -1;
        for (auto&& elem : ::tpy::iter_range(iterable)) {
            const T& cur = elem;
            const int cand = best == 0 ? 1 : 0;
            slots[cand].emplace(cur);
            K k = key(*slots[cand]);
            if (best < 0 || better(k, *best_key)) {
                best = cand;
                best_key.emplace(std::move(k));
            }
        }
        if (best < 0) raise_value_error("{}() iterable argument is empty", name);
        return std::move(*slots[best]);
    }
}

template<typename T, typename Iter, typename KeyFn>
T builtin_min_key(Iter&& iterable, KeyFn&& key) {
    return builtin_extreme_key<T>(std::forward<Iter>(iterable), std::forward<KeyFn>(key),
        "min", [](const auto& a, const auto& b) { return a < b; });
}

template<typename T, typename Iter, typename KeyFn>
T builtin_max_key(Iter&& iterable, KeyFn&& key) {
    return builtin_extreme_key<T>(std::forward<Iter>(iterable), std::forward<KeyFn>(key),
        "max", [](const auto& a, const auto& b) { return b < a; });
}

// Whether an iterator's step payload is a reference to an element the source
// owns (a class instance), rather than a value.
template<typename S>
inline constexpr bool steps_reference =
    requires { typename S::is_val_or_ref_tag; } && requires { requires !S::is_val; };

// next(it, default): the element, or the default once the iterator is
// exhausted, in the form the iterator steps it -- a value by value, a
// reference payload as a reference to the element or to the default itself
// (Python returns the object, never a copy). A temporary default lives as
// long as the caller's full expression.
template<typename Iter, typename D>
decltype(auto) next_or(Iter& it, D&& dflt) {
    auto step = it.__next__();
    using S = std::remove_cvref_t<decltype(*step)>;
    if constexpr (steps_reference<S>) {
        using R = std::common_reference_t<decltype(step->get()),
                                          std::remove_reference_t<D>&>;
        if (step) return static_cast<R>(step->get());
        return static_cast<R>(dflt);
    } else {
        using R = std::remove_cvref_t<decltype(unwrap_ref_move(*step))>;
        if (step) return R(unwrap_ref_move(*step));
        return R(std::forward<D>(dflt));
    }
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
