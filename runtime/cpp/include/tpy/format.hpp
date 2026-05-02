/**
 * TurboPython Runtime - Formatting Utilities
 *
 * Python-style printing for bools, floats, and char-to-string conversion.
 */

#pragma once

#include <cassert>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <iomanip>
#include <optional>
#include <ranges>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <type_traits>
#include <vector>

#include "core.hpp"

namespace tpy {

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
    // Special cases
    if (std::isnan(value)) return "nan";
    if (std::isinf(value)) return value > 0 ? "inf" : "-inf";

    // Python switches to scientific for |x| < 1e-4 or |x| >= 1e16; otherwise
    // it uses fixed notation. std::to_chars's `shortest` form picks the
    // shortest of the two, which doesn't always agree with Python -- so we
    // pick the format ourselves and hand it to to_chars (no precision arg ->
    // shortest round-tripping form within that format).
    double abs_val = std::fabs(value);
    bool use_scientific = (abs_val != 0.0 && abs_val < 1e-4) || abs_val >= 1e16;

    // 32 bytes covers the worst case: scientific
    // "-1.7976931348623157e+308" (~24 chars), fixed up to ~22 chars within
    // our [1e-4, 1e16) band. Exponent padding is done on the std::string
    // afterwards, not in this buffer.
    char buf[32];
    auto r = std::to_chars(
        buf, buf + sizeof(buf), value,
        use_scientific ? std::chars_format::scientific : std::chars_format::fixed);
    assert(r.ec == std::errc{});
    std::string s(buf, r.ptr);

    if (use_scientific) {
        // std::to_chars emits the exponent with no leading zeros (e.g. "1e-5");
        // Python pads single-digit exponents to two digits ("1e-05").
        auto e_pos = s.find('e');
        if (e_pos != std::string::npos) {
            std::size_t digits_pos = e_pos + 2;  // skip 'e' and sign
            std::size_t digit_count = s.size() - digits_pos;
            if (digit_count < 2) {
                s.insert(digits_pos, std::string(2 - digit_count, '0'));
            }
        }
    } else {
        // Whole numbers come back without a decimal point (e.g. "1");
        // Python's repr() always shows ".0" for floats.
        if (s.find('.') == std::string::npos) {
            s += ".0";
        }
    }
    return s;
}

inline std::ostream& operator<<(std::ostream& os, const print_float& pf) {
    os << format_float(pf.value);
    return os;
}

/**
 * repr_quote_string - Python-style repr of a string.
 *
 * Produces the Python repr form: outer quotes plus C-style escapes for
 * `\\`, `\n`, `\r`, `\t`, the active quote character, and other ASCII
 * control bytes (`\xNN`). Bytes >= 0x80 are passed through as-is so a
 * UTF-8-encoded string round-trips visually.
 *
 * Quote selection follows CPython: prefer `'`, switch to `"` only when
 * the string contains `'` and no `"`.
 */
inline std::string repr_quote_string(std::string_view s) {
    bool has_single = s.find('\'') != std::string_view::npos;
    bool has_double = s.find('"') != std::string_view::npos;
    char quote = (has_single && !has_double) ? '"' : '\'';
    std::string out;
    out.reserve(s.size() + 2);
    out.push_back(quote);
    for (auto ch : s) {
        unsigned char c = static_cast<unsigned char>(ch);
        switch (c) {
            case '\\': out.append("\\\\"); break;
            case '\n': out.append("\\n"); break;
            case '\r': out.append("\\r"); break;
            case '\t': out.append("\\t"); break;
            default:
                if (ch == quote) {
                    out.push_back('\\');
                    out.push_back(ch);
                } else if (c < 0x20 || c == 0x7f) {
                    static const char kHex[] = "0123456789abcdef";
                    out.append("\\x");
                    out.push_back(kHex[c >> 4]);
                    out.push_back(kHex[c & 0xf]);
                } else {
                    out.push_back(ch);
                }
                break;
        }
    }
    out.push_back(quote);
    return out;
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

/**
 * str_concat - Concatenate two string-like values into a new std::string.
 *
 * Takes both sides as string_view (zero-copy from std::string, string_view,
 * and const char*) and performs a single optimally-sized allocation.
 */
inline std::string str_concat(std::string_view a, std::string_view b) {
    std::string result;
    result.reserve(a.size() + b.size());
    result.append(a);
    result.append(b);
    return result;
}

// -- str.split / str.join helpers ------------------------------------------
// split returns vector<string> (copies) rather than vector<string_view> because
// TurboPython has no borrow checker to prevent mutation of the source while
// views exist. A future splitview() could return views for perf-critical code.

inline std::vector<std::string> str_split(std::string_view s, std::string_view sep) {
    if (sep.empty()) {
        tpy_panic("empty separator");
    }
    std::vector<std::string> result;
    size_t start = 0;
    while (true) {
        size_t pos = s.find(sep, start);
        if (pos == std::string_view::npos) {
            result.emplace_back(s.substr(start));
            break;
        }
        result.emplace_back(s.substr(start, pos - start));
        start = pos + sep.size();
    }
    return result;
}

inline std::vector<std::string> str_split(std::string_view s, std::string_view sep, int32_t maxsplit) {
    if (sep.empty()) {
        tpy_panic("empty separator");
    }
    if (maxsplit < 0) {
        return str_split(s, sep);
    }
    std::vector<std::string> result;
    size_t start = 0;
    int32_t splits = 0;
    while (splits < maxsplit) {
        size_t pos = s.find(sep, start);
        if (pos == std::string_view::npos) break;
        result.emplace_back(s.substr(start, pos - start));
        start = pos + sep.size();
        ++splits;
    }
    result.emplace_back(s.substr(start));
    return result;
}

inline std::vector<std::string> str_split_whitespace(std::string_view s) {
    std::vector<std::string> result;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        if (i >= s.size()) break;
        size_t start = i;
        while (i < s.size() && !std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        result.emplace_back(s.substr(start, i - start));
    }
    return result;
}

inline std::vector<std::string> str_split_whitespace(std::string_view s, int32_t maxsplit) {
    if (maxsplit < 0) {
        return str_split_whitespace(s);
    }
    std::vector<std::string> result;
    size_t i = 0;
    int32_t splits = 0;
    while (i < s.size()) {
        while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        if (i >= s.size()) break;
        if (splits >= maxsplit) {
            result.emplace_back(s.substr(i));
            return result;
        }
        size_t start = i;
        while (i < s.size() && !std::isspace(static_cast<unsigned char>(s[i]))) ++i;
        result.emplace_back(s.substr(start, i - start));
        ++splits;
    }
    return result;
}

template<typename Container>
    requires std::ranges::input_range<const Container>
inline std::string str_join(std::string_view sep, const Container& items) {
    std::string result;
    bool first = true;
    for (const auto& item : items) {
        if (!first) result.append(sep);
        result.append(std::string_view(item));
        first = false;
    }
    return result;
}

inline std::string str_join(std::string_view sep, std::initializer_list<const char*> items) {
    std::string result;
    bool first = true;
    for (auto item : items) {
        if (!first) result.append(sep);
        result.append(item);
        first = false;
    }
    return result;
}

inline std::string str_join(std::string_view sep, std::initializer_list<std::string_view> items) {
    std::string result;
    bool first = true;
    for (auto item : items) {
        if (!first) result.append(sep);
        result.append(item);
        first = false;
    }
    return result;
}

// -- str.strip / lstrip / rstrip -------------------------------------------

inline std::string_view str_strip(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) ++start;
    size_t end = s.size();
    while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;
    return s.substr(start, end - start);
}

inline std::string_view str_lstrip(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) ++start;
    return s.substr(start);
}

inline std::string_view str_rstrip(std::string_view s) {
    size_t end = s.size();
    while (end > 0 && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;
    return s.substr(0, end);
}

// -- str.replace -----------------------------------------------------------

inline std::string str_replace(std::string_view s, std::string_view old_sub, std::string_view new_sub) {
    if (old_sub.empty()) {
        // Python semantics: insert new_sub between every character and at both ends
        std::string result;
        result.reserve(s.size() + new_sub.size() * (s.size() + 1));
        for (size_t i = 0; i < s.size(); ++i) {
            result.append(new_sub);
            result += s[i];
        }
        result.append(new_sub);
        return result;
    }
    std::string result;
    size_t start = 0;
    while (true) {
        size_t pos = s.find(old_sub, start);
        if (pos == std::string_view::npos) {
            result.append(s.substr(start));
            break;
        }
        result.append(s.substr(start, pos - start));
        result.append(new_sub);
        start = pos + old_sub.size();
    }
    return result;
}

// -- str.find / rfind / index ----------------------------------------------

inline int32_t str_find(std::string_view s, std::string_view sub) {
    auto pos = s.find(sub);
    return pos == std::string_view::npos ? -1 : static_cast<int32_t>(pos);
}

inline int32_t str_rfind(std::string_view s, std::string_view sub) {
    auto pos = s.rfind(sub);
    return pos == std::string_view::npos ? -1 : static_cast<int32_t>(pos);
}

inline int32_t str_index(std::string_view s, std::string_view sub) {
    auto pos = s.find(sub);
    if (pos == std::string_view::npos) {
        tpy_panic("substring not found");
    }
    return static_cast<int32_t>(pos);
}

// -- str.startswith / endswith ---------------------------------------------

inline bool str_startswith(std::string_view s, std::string_view prefix) {
    return s.starts_with(prefix);
}

inline bool str_endswith(std::string_view s, std::string_view suffix) {
    return s.ends_with(suffix);
}

// -- str.upper / lower -----------------------------------------------------

inline std::string str_upper(std::string_view s) {
    std::string result(s);
    for (auto& c : result) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return result;
}

inline std::string str_lower(std::string_view s) {
    std::string result(s);
    for (auto& c : result) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return result;
}

// -- str.__mul__ (string repetition) ---------------------------------------

inline std::string str_repeat(std::string_view s, int32_t n) {
    if (n <= 0) return {};
    std::string result;
    result.reserve(s.size() * static_cast<size_t>(n));
    for (int32_t i = 0; i < n; ++i) result.append(s);
    return result;
}

// -- str.count -------------------------------------------------------------

inline int32_t str_count(std::string_view s, std::string_view sub) {
    if (sub.empty()) {
        return static_cast<int32_t>(s.size()) + 1;
    }
    int32_t n = 0;
    size_t start = 0;
    while (true) {
        size_t pos = s.find(sub, start);
        if (pos == std::string_view::npos) break;
        ++n;
        start = pos + sub.size();
    }
    return n;
}

// -- str.isdigit / isalpha / isalnum / isspace -----------------------------

inline bool str_isdigit(std::string_view s) {
    if (s.empty()) return false;
    for (auto c : s) if (!std::isdigit(static_cast<unsigned char>(c))) return false;
    return true;
}

inline bool str_isalpha(std::string_view s) {
    if (s.empty()) return false;
    for (auto c : s) if (!std::isalpha(static_cast<unsigned char>(c))) return false;
    return true;
}

inline bool str_isalnum(std::string_view s) {
    if (s.empty()) return false;
    for (auto c : s) if (!std::isalnum(static_cast<unsigned char>(c))) return false;
    return true;
}

inline bool str_isspace(std::string_view s) {
    if (s.empty()) return false;
    for (auto c : s) if (!std::isspace(static_cast<unsigned char>(c))) return false;
    return true;
}

// -- str.isupper / islower -------------------------------------------------

inline bool str_isupper(std::string_view s) {
    bool has_cased = false;
    for (auto c : s) {
        unsigned char uc = static_cast<unsigned char>(c);
        if (std::islower(uc)) return false;
        if (std::isupper(uc)) has_cased = true;
    }
    return has_cased;
}

inline bool str_islower(std::string_view s) {
    bool has_cased = false;
    for (auto c : s) {
        unsigned char uc = static_cast<unsigned char>(c);
        if (std::isupper(uc)) return false;
        if (std::islower(uc)) has_cased = true;
    }
    return has_cased;
}

// -- str.capitalize / title / swapcase -------------------------------------

inline std::string str_capitalize(std::string_view s) {
    std::string result(s);
    if (!result.empty()) {
        result[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(result[0])));
        for (size_t i = 1; i < result.size(); ++i)
            result[i] = static_cast<char>(std::tolower(static_cast<unsigned char>(result[i])));
    }
    return result;
}

inline std::string str_title(std::string_view s) {
    std::string result(s);
    bool after_boundary = true;
    for (size_t i = 0; i < result.size(); ++i) {
        unsigned char uc = static_cast<unsigned char>(result[i]);
        if (std::isalpha(uc)) {
            result[i] = static_cast<char>(after_boundary ? std::toupper(uc) : std::tolower(uc));
            after_boundary = false;
        } else {
            after_boundary = true;
        }
    }
    return result;
}

inline std::string str_swapcase(std::string_view s) {
    std::string result(s);
    for (auto& c : result) {
        unsigned char uc = static_cast<unsigned char>(c);
        if (std::isupper(uc)) c = static_cast<char>(std::tolower(uc));
        else if (std::islower(uc)) c = static_cast<char>(std::toupper(uc));
    }
    return result;
}

// -- str.removeprefix / removesuffix ---------------------------------------

inline std::string_view str_removeprefix(std::string_view s, std::string_view prefix) {
    if (s.starts_with(prefix)) return s.substr(prefix.size());
    return s;
}

inline std::string_view str_removesuffix(std::string_view s, std::string_view suffix) {
    if (!suffix.empty() && s.ends_with(suffix)) return s.substr(0, s.size() - suffix.size());
    return s;
}

// -- str.rindex ------------------------------------------------------------

inline int32_t str_rindex(std::string_view s, std::string_view sub) {
    auto pos = s.rfind(sub);
    if (pos == std::string_view::npos) {
        tpy_panic("substring not found");
    }
    return static_cast<int32_t>(pos);
}

// -- str.splitlines --------------------------------------------------------

inline std::vector<std::string> str_splitlines(std::string_view s) {
    std::vector<std::string> result;
    size_t start = 0;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '\n') {
            result.emplace_back(s.substr(start, i - start));
            start = i + 1;
        } else if (s[i] == '\r') {
            result.emplace_back(s.substr(start, i - start));
            if (i + 1 < s.size() && s[i + 1] == '\n') ++i;
            start = i + 1;
        }
    }
    if (start < s.size()) {
        result.emplace_back(s.substr(start));
    }
    return result;
}

/**
 * bool_to_str - Convert bool to "True" or "False" string.
 * Returns const char* pointing to static storage (safe for std::string_view).
 */
inline const char* bool_to_str(bool x) {
    return x ? "True" : "False";
}

/**
 * fixed_to_str - Convert any fixed-width integer to string.
 * 8-bit types are promoted to int to avoid char interpretation.
 */
template<typename T>
inline std::string fixed_to_str(T x) {
    if constexpr (sizeof(T) == 1)
        return std::to_string(static_cast<int>(x));
    else
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
 * float_from_str - Convert string to double.
 * Panics on invalid input (Python raises ValueError).
 * Supports optional leading/trailing whitespace, sign, and special values
 * "inf", "infinity", "-inf", "-infinity", "nan" (case-insensitive).
 */
inline double float_from_str(std::string_view s) {
    size_t start = 0;
    while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start]))) ++start;
    size_t end = s.size();
    while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1]))) --end;

    if (start >= end) {
        std::string msg = "could not convert string to float: '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    std::string trimmed(s.substr(start, end - start));
    char* endptr;
    double result = std::strtod(trimmed.c_str(), &endptr);

    if (endptr != trimmed.c_str() + trimmed.size()) {
        std::string msg = "could not convert string to float: '" + std::string(s) + "'";
        tpy_panic(msg.c_str());
    }

    return result;
}

/**
 * float32_from_str - Convert string to float (32-bit).
 * Same as float_from_str but narrows to single precision.
 * Out-of-range values produce +/-inf (matching Python behavior).
 */
inline float float32_from_str(std::string_view s) {
    double d = float_from_str(s);
    return static_cast<float>(d);
}

/**
 * print_optional_val - Print a std::optional<T> as Python would.
 *
 * Prints "None" for empty optional, otherwise prints the value.
 * The Formatter template parameter controls how the value is printed:
 * - void (default): prints the value directly via operator<<
 * - print_bool: prints True/False
 * - print_float: prints Python-style float
 */
template<typename Formatter, typename T>
struct print_optional_val {
    const std::optional<T>& opt;
    explicit print_optional_val(const std::optional<T>& o) : opt(o) {}
};

// Deduction guide: print_optional_val(opt) deduces Formatter=void
template<typename T>
print_optional_val(const std::optional<T>&) -> print_optional_val<void, T>;

template<typename Formatter, typename T>
inline std::ostream& operator<<(std::ostream& os, const print_optional_val<Formatter, T>& po) {
    if (po.opt.has_value()) {
        if constexpr (std::is_same_v<Formatter, void>) {
            os << *po.opt;
        } else {
            os << Formatter(*po.opt);
        }
    } else {
        os << "None";
    }
    return os;
}

/**
 * print_optional - Print a nullable pointer as Python would.
 *
 * Prints "None" for nullptr, otherwise prints the pointed-to value.
 */
template<typename T>
struct print_optional {
    const T* ptr;
    explicit print_optional(const T* p) : ptr(p) {}
};

template<typename T>
inline std::ostream& operator<<(std::ostream& os, const print_optional<T>& po) {
    if (po.ptr) os << *(po.ptr); else os << "None";
    return os;
}

/**
 * ptr_to_optional - Convert a T* nullable pointer to std::optional<T>.
 *
 * nullptr → std::nullopt, otherwise copies the pointed-to value.
 * Used at the T* → std::optional<T> boundary (e.g., assigning a pointer-local
 * to an optional record field).
 */
template<typename T>
std::optional<T> ptr_to_optional(const T* ptr) {
    if (ptr) return *ptr;
    return std::nullopt;
}

/**
 * optional_to_ptr - Convert std::optional<T>& to T* (mutable).
 *
 * Empty optional → nullptr, otherwise pointer to stored value.
 * Used at the std::optional<T> → T* boundary (e.g., reading an optional
 * record field into a pointer-local variable).
 */
template<typename T>
T* optional_to_ptr(std::optional<T>& opt) {
    if (opt.has_value()) return &*opt;
    return nullptr;
}

template<typename T>
const T* optional_to_ptr(const std::optional<T>& opt) {
    if (opt.has_value()) return &*opt;
    return nullptr;
}

// Default repr for records without __repr__/__str__: "<ClassName object at 0xADDR>"
template<typename T>
inline std::ostream& print_object_default(std::ostream& os, std::string_view class_name, const T& obj) {
    auto flags = os.flags();
    os << "<" << class_name << " object at 0x"
       << std::hex << reinterpret_cast<uintptr_t>(&obj) << ">";
    os.flags(flags);
    return os;
}

} // namespace tpy
