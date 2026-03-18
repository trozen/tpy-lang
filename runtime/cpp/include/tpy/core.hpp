/**
 * TurboPython Runtime - Core Utilities
 *
 * Panic handling and pointer operations.
 */

#pragma once

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <expected>
#include <optional>
#include <string>
#include <string_view>
#include <type_traits>
#include <variant>

namespace tpy {

// Python exception hierarchy -- inherits from std::exception so future
// try/except/raise can use C++ exception machinery.
struct BaseException : std::exception {};
struct Exception : BaseException {};
struct StopIteration : Exception {};

// next(iterator) -- forwards __next__() result (std::expected<T, StopIteration>)
template<typename Iter>
auto next(Iter& it) -> decltype(it.__next__()) {
    return it.__next__();
}

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
T& deref_check(T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

template <typename T>
const T& deref_check(const T* ptr) {
    if (ptr == nullptr) {
        tpy_panic("null pointer dereference");
    }
    return *ptr;
}

// User types with __deref__() method
template <typename T>
    requires requires(T& t) { t.__deref__(); }
decltype(auto) deref_check(T& obj) {
    return obj.__deref__();
}

/**
 * Checked optional dereference - panics if optional is empty.
 */
template <typename T>
T& deref_optional_check(std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("null optional dereference");
    }
    return *opt;
}

template <typename T>
const T& deref_optional_check(const std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("null optional dereference");
    }
    return *opt;
}

/**
 * Optional truthiness helper - matches Python semantics for Optional[value]:
 * value is truthy only when engaged and contained value is truthy.
 */
template <typename T>
inline bool is_truthy(const std::optional<T>& opt) {
    return opt.has_value() && static_cast<bool>(*opt);
}

// String truthiness: non-empty is truthy (std::string has no operator bool)
inline bool is_truthy(const std::optional<std::string>& opt) {
    return opt.has_value() && !opt->empty();
}

inline bool is_truthy(const std::optional<std::string_view>& opt) {
    return opt.has_value() && !opt->empty();
}

/**
 * Destroy an object at a pointer location.
 * No-op for trivially destructible types (scalars, PODs).
 */
template <typename T>
void destroy_at(T* p) {
    if constexpr (!std::is_trivially_destructible_v<T>) {
        p->~T();
    }
}

/**
 * Checked true division for floats -- panics on zero divisor
 * to match Python's ZeroDivisionError semantics.
 */
inline double truediv(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
    return a / b;
}

inline double floordiv(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
    return std::floor(a / b);
}

inline double fmod(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
    return std::fmod(a, b);
}

// Float32 arithmetic helpers
inline float truediv_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
    return a / b;
}

inline float floordiv_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
    return std::floor(a / b);
}

inline float fmod_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
    return std::fmod(a, b);
}

} // namespace tpy
