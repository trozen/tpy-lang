/**
 * TurboPython Runtime - Core Utilities
 *
 * Panic handling and pointer operations.
 */

#pragma once

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cxxabi.h>
#include <exception>
#include <expected>
#include <optional>
#include <ostream>
#include <string>
#include <string_view>
#include <type_traits>
#include <typeinfo>
#include <variant>

namespace tpy {

// Python exception hierarchy -- inherits from std::exception for C++ throw/catch.
struct BaseException : std::exception {
    std::string message;
    BaseException() = default;
    explicit BaseException(std::string msg) : message(std::move(msg)) {}
    const char* what() const noexcept override { return message.c_str(); }
    std::string_view __str__() const { return message; }
    friend std::ostream& operator<<(std::ostream& os, const BaseException& e) { return os << e.message; }
};
struct Exception : BaseException { using BaseException::BaseException; };
struct ValueError : Exception { using Exception::Exception; };
struct OSError : Exception { using Exception::Exception; };
struct FileNotFoundError : OSError { using OSError::OSError; };
struct StopIteration : Exception {};

// Portable replacement for std::unexpected(). Some libc++ versions (e.g. zig's
// bundled clang) expose both the deprecated std::unexpected() function and the
// C++23 std::unexpected<E> class template, making the name ambiguous. This wrapper
// avoids naming std::unexpected entirely by using std::unexpect tag construction.
template<typename E>
struct Unexpected {
    E error;
    template<typename T>
    operator std::expected<T, E>() && {
        return std::expected<T, E>{std::unexpect, std::move(error)};
    }
    template<typename T>
    operator std::expected<T, E>() const& {
        return std::expected<T, E>{std::unexpect, error};
    }
};

template<typename E>
Unexpected<std::remove_cvref_t<E>> make_unexpected(E&& e) {
    return Unexpected<std::remove_cvref_t<E>>{std::forward<E>(e)};
}

// next(iterator) -- forwards __next__() result (std::expected<T, StopIteration>)
template<typename Iter>
auto next(Iter& it) -> decltype(it.__next__()) {
    return it.__next__();
}

/**
 * Panic and abort - called on fatal runtime errors.
 */
[[noreturn]] inline void tpy_panic(std::string_view msg) {
    std::fputs("TurboPython panic: ", stderr);
    std::fwrite(msg.data(), 1, msg.size(), stderr);
    std::fputc('\n', stderr);
    std::exit(1);
}

// Normalizes uncaught-exception output across libstdc++/libc++. Installed from
// codegen-emitted main() via std::set_terminate; not installed when codegen
// runs in --no-main mode, so host applications keep their own terminate handler.
[[noreturn]] inline void tpy_terminate_handler() noexcept {
    const char* type_name = "unknown";
    const char* what_msg = nullptr;
    char* demangled = nullptr;

    // Hold ex across the print: what_msg points into the exception object,
    // which is only guaranteed to outlive any active exception_ptr owning it.
    std::exception_ptr ex = std::current_exception();
    if (ex) {
        try {
            std::rethrow_exception(ex);
        } catch (const std::exception& e) {
            int status = 0;
            demangled = abi::__cxa_demangle(typeid(e).name(), nullptr, nullptr, &status);
            type_name = (status == 0 && demangled) ? demangled : typeid(e).name();
            what_msg = e.what();
        } catch (...) {
        }
    }

    std::fputs("TurboPython panic: uncaught ", stderr);
    std::fputs(type_name, stderr);
    if (what_msg && *what_msg) {
        std::fputs(": ", stderr);
        std::fputs(what_msg, stderr);
    }
    std::fputc('\n', stderr);

    std::free(demangled);
    std::_Exit(1);
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
 * Checked TypedDict optional-field access -- panics with KeyError if the
 * field is absent. Used by codegen for `td["key"]` on a `total=False`
 * TypedDict. Distinct from deref_optional_check (which says "null optional
 * dereference") so the panic matches the existing TPy KeyError convention
 * for dict[k] misses, and from raw std::optional::value() whose
 * bad_optional_access::what() text diverges between libstdc++ and libc++.
 */
template <typename T>
T& typed_dict_field_check(std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("KeyError");
    }
    return *opt;
}

template <typename T>
const T& typed_dict_field_check(const std::optional<T>& opt) {
    if (!opt.has_value()) {
        tpy_panic("KeyError");
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
inline constexpr double truediv(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
    return a / b;
}

// Constant-evaluation helpers: std::floor / std::fmod are constexpr under
// C++23 P1383 in libstdc++ (GCC 13+), but libc++ (Apple clang / macOS) has
// not shipped that yet. When the stdlib advertises P1383 we call through
// unconditionally; otherwise we fall back to hand-rolled constexpr paths
// selected via `if consteval`. Fallbacks are correct for finite values in
// long long range, which is the regime Final literals live in; the runtime
// path is unchanged. Once every supported stdlib defines the feature macro,
// drop the `#else` branches entirely.
#if defined(__cpp_lib_constexpr_cmath) && __cpp_lib_constexpr_cmath >= 202202L
#define TPY_CMATH_CONSTEXPR 1
#else
#define TPY_CMATH_CONSTEXPR 0
#endif

inline constexpr double floordiv(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
#if TPY_CMATH_CONSTEXPR
    return std::floor(a / b);
#else
    if consteval {
        double q = a / b;
        long long i = static_cast<long long>(q);
        double d = static_cast<double>(i);
        return (d > q) ? d - 1.0 : d;
    }
    return std::floor(a / b);
#endif
}

inline constexpr double fmod(double a, double b) {
    if (b == 0.0) tpy_panic("Division by zero");
#if TPY_CMATH_CONSTEXPR
    return std::fmod(a, b);
#else
    if consteval {
        long long i = static_cast<long long>(a / b);
        return a - static_cast<double>(i) * b;
    }
    return std::fmod(a, b);
#endif
}

// Float32 arithmetic helpers
inline constexpr float truediv_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
    return a / b;
}

inline constexpr float floordiv_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
#if TPY_CMATH_CONSTEXPR
    return std::floor(a / b);
#else
    if consteval {
        float q = a / b;
        long long i = static_cast<long long>(q);
        float d = static_cast<float>(i);
        return (d > q) ? d - 1.0f : d;
    }
    return std::floor(a / b);
#endif
}

inline constexpr float fmod_f32(float a, float b) {
    if (b == 0.0f) tpy_panic("Division by zero");
#if TPY_CMATH_CONSTEXPR
    return std::fmod(a, b);
#else
    if consteval {
        long long i = static_cast<long long>(a / b);
        return a - static_cast<float>(i) * b;
    }
    return std::fmod(a, b);
#endif
}

} // namespace tpy
