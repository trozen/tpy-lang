/**
 * TurboPython Runtime - Formatting Utilities
 *
 * Python-style printing for bools, floats, and char-to-string conversion.
 */

#pragma once

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>

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

} // namespace tpy
