#pragma once

#include <cmath>
#include <cstdint>
#include <limits>
#include <tuple>

#include <tpy/core.hpp>

namespace tpy::stdlib::math {

namespace detail {

inline double check_unary_result(double x, double result, bool can_overflow) {
    if (std::isnan(result) && !std::isnan(x)) {
        ::tpy::raise_value_error("math domain error");
    }
    if (std::isinf(result) && std::isfinite(x)) {
        if (can_overflow) {
            ::tpy::raise_overflow_error("math range error");
        }
        ::tpy::raise_value_error("math domain error");
    }
    return result;
}

}  // namespace detail

inline double checked_log(double x) {
    return detail::check_unary_result(x, std::log(x), false);
}

inline double checked_log10(double x) {
    return detail::check_unary_result(x, std::log10(x), false);
}

inline double checked_log2(double x) {
    return detail::check_unary_result(x, std::log2(x), false);
}

inline double checked_log1p(double x) {
    return detail::check_unary_result(x, std::log1p(x), false);
}

inline double checked_sqrt(double x) {
    return detail::check_unary_result(x, std::sqrt(x), false);
}

inline double checked_asin(double x) {
    return detail::check_unary_result(x, std::asin(x), false);
}

inline double checked_acos(double x) {
    return detail::check_unary_result(x, std::acos(x), false);
}

inline double checked_acosh(double x) {
    return detail::check_unary_result(x, std::acosh(x), false);
}

inline double checked_atanh(double x) {
    return detail::check_unary_result(x, std::atanh(x), false);
}

inline double checked_sin(double x) {
    return detail::check_unary_result(x, std::sin(x), false);
}

inline double checked_cos(double x) {
    return detail::check_unary_result(x, std::cos(x), false);
}

inline double checked_tan(double x) {
    return detail::check_unary_result(x, std::tan(x), false);
}

inline double checked_exp(double x) {
    return detail::check_unary_result(x, std::exp(x), true);
}

inline double checked_exp2(double x) {
    return detail::check_unary_result(x, std::exp2(x), true);
}

inline double checked_expm1(double x) {
    return detail::check_unary_result(x, std::expm1(x), true);
}

inline double checked_sinh(double x) {
    return detail::check_unary_result(x, std::sinh(x), true);
}

inline double checked_cosh(double x) {
    return detail::check_unary_result(x, std::cosh(x), true);
}

inline double checked_gamma(double x) {
    // Gamma's finite poles must not be mistaken for range overflow.
    if (x <= 0.0 && (!std::isfinite(x) || x == std::floor(x))) {
        ::tpy::raise_value_error("math domain error");
    }
    return detail::check_unary_result(x, std::tgamma(x), true);
}

inline double checked_lgamma(double x) {
    if (std::isfinite(x) && x <= 0.0 && x == std::floor(x)) {
        ::tpy::raise_value_error("math domain error");
    }
    return detail::check_unary_result(x, std::lgamma(x), true);
}

inline double checked_pow(double x, double y) {
    double result = std::pow(x, y);
    if (std::isnan(result) && !std::isnan(x) && !std::isnan(y)) {
        ::tpy::raise_value_error("math domain error");
    }
    if (std::isinf(result) && std::isfinite(x) && std::isfinite(y)) {
        // A zero base has a pole; other finite inputs overflow the range.
        if (x == 0.0) {
            ::tpy::raise_value_error("math domain error");
        }
        ::tpy::raise_overflow_error("math range error");
    }
    return result;
}

inline double checked_fmod(double x, double y) {
    double result = std::fmod(x, y);
    if (std::isnan(result) && !std::isnan(x) && !std::isnan(y)) {
        ::tpy::raise_value_error("math domain error");
    }
    return result;
}

inline double checked_remainder(double x, double y) {
    double result = std::remainder(x, y);
    if (std::isnan(result) && !std::isnan(x) && !std::isnan(y)) {
        ::tpy::raise_value_error("math domain error");
    }
    return result;
}

inline double checked_ldexp(double x, int32_t i) {
    return detail::check_unary_result(x, std::ldexp(x, i), true);
}

inline double checked_fma(double x, double y, double z) {
    double result = std::fma(x, y, z);
    // A NaN operand takes precedence even over zero times infinity.
    if (std::isnan(result) && !std::isnan(x) && !std::isnan(y) && !std::isnan(z)) {
        ::tpy::raise_value_error("invalid operation in fma");
    }
    if (std::isinf(result) && std::isfinite(x) && std::isfinite(y) && std::isfinite(z)) {
        ::tpy::raise_overflow_error("overflow in fma");
    }
    return result;
}

inline std::tuple<double, double> modf(double x) {
    double int_part;
    double frac = std::modf(x, &int_part);
    return {frac, int_part};
}

// Exponent template parameter. For IEEE 754 double, std::frexp normalizes
// |m| to [0.5, 1), so exp is in [-1073, 1024] (the lower bound comes from
// subnormals: smallest positive denormal is 2^-1074 -> m=0.5, exp=-1073;
// upper bound from max finite double just under 2^1024). Safely fits in
// any 16+ bit signed int. Caller picks the int type (defaults to
// DefaultInt on the Python side); `T(exp)` uses the target's constructor
// so BigInt also works.
template<typename T>
inline std::tuple<double, T> frexp(double x) {
    int exp;
    double m = std::frexp(x, &exp);
    return {m, T(exp)};
}

inline double ulp(double x) {
    if (std::isnan(x)) return x;
    if (std::isinf(x)) return std::numeric_limits<double>::infinity();
    if (x == 0.0) return std::numeric_limits<double>::denorm_min();
    double ax = std::fabs(x);
    double next = std::nextafter(ax, std::numeric_limits<double>::infinity());
    // Max-finite has no finite successor, so use its predecessor spacing.
    if (std::isinf(next)) return ax - std::nextafter(ax, 0.0);
    return next - ax;
}

}  // namespace tpy::stdlib::math
