/**
 * TurboPython Runtime - Core Utilities
 *
 * Panic handling and pointer operations.
 */

#pragma once

#include <cmath>
#include <concepts>
#include <cstdio>
#include <cstdlib>
#include <cxxabi.h>
#include <exception>
#include <expected>
#include <format>
#include <memory>
#include <optional>
#include <ostream>
#include <string>
#include <string_view>
#include <type_traits>
#include <typeinfo>
#include <utility>
#include <variant>

#include "throwable.hpp"
#include "type_traits.hpp"

namespace tpy {

// Python exception hierarchy. Inherits Throwable -> std::exception so
// `except as e` borrows route through the Throwable vtable for clone()
// and __raise__() while plain C++ throw/catch still works.
struct BaseException : ::tpy::Throwable {
    std::string message;
    BaseException() = default;
    explicit BaseException(std::string msg) : message(std::move(msg)) {}
    explicit BaseException(std::string_view msg) : message(msg) {}
    // Disambiguates string-literal calls that would otherwise be ambiguous
    // between the std::string and std::string_view ctors above.
    explicit BaseException(const char* msg) : message(msg) {}
    const char* what() const noexcept override { return message.c_str(); }
    std::string_view __str__() const { return message; }
    friend std::ostream& operator<<(std::ostream& os, const BaseException& e) { return os << e.message; }
    TPY_THROWABLE_VIRTUALS(BaseException)
};
struct Exception : BaseException { using BaseException::BaseException; TPY_THROWABLE_VIRTUALS(Exception) };
struct ValueError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(ValueError) };
struct OSError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(OSError) };
struct FileNotFoundError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(FileNotFoundError) };
struct AttributeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(AttributeError) };
struct AssertionError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(AssertionError) };
struct IndexError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(IndexError) };
struct KeyError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(KeyError) };
struct ArithmeticError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(ArithmeticError) };
struct ZeroDivisionError : ArithmeticError { using ArithmeticError::ArithmeticError; TPY_THROWABLE_VIRTUALS(ZeroDivisionError) };
struct OverflowError : ArithmeticError { using ArithmeticError::ArithmeticError; TPY_THROWABLE_VIRTUALS(OverflowError) };
struct TypeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(TypeError) };
struct NotImplementedError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(NotImplementedError) };
struct RuntimeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(RuntimeError) };
struct MemoryError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(MemoryError) };
struct StopIteration : Exception { TPY_THROWABLE_VIRTUALS(StopIteration) };
struct StopAsyncIteration : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(StopAsyncIteration) };
struct TimeoutError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(TimeoutError) };

// Forward decl: raise_fixedint_overflow (below) calls tpy_panic, whose
// definition lives later in this header.
[[noreturn]] inline void tpy_panic(std::string_view msg);

// raise<E>(msg) / raise<E>(fmt, args...) -- throw a Python-shaped exception.
//
// Every runtime site that surfaces a catchable error goes through this so
// the underlying policy (currently always `throw`) can be swapped at compile
// time later (e.g. abort-on-error builds, or routing to `tpy_panic` in
// `@noalloc` regions) without rewriting call sites.
//
// Two forms:
//   raise<E>(msg)          -- pre-built std::string_view message
//   raise<E>(fmt, args...) -- std::format-style; format string validated
//                             against the arg types at compile time. Single
//                             trailing arg disambiguates against the
//                             string_view overload.
//
// Not constexpr; calling from a constexpr function is permitted under
// C++23 P2448 as long as the call is never reached during constant
// evaluation.
template<typename E>
    requires std::derived_from<E, BaseException>
[[noreturn]] inline void raise(std::string_view msg) {
    throw E(msg);
}
template<typename E, typename T, typename... Rest>
    requires std::derived_from<E, BaseException>
[[noreturn]] inline void raise(std::format_string<T, Rest...> fmt, T&& arg,
                               Rest&&... rest) {
    throw E(std::format(fmt, std::forward<T>(arg), std::forward<Rest>(rest)...));
}

// `assert cond[, msg]` failure path. Thin wrapper around `raise<AssertionError>`
// that supplies the no-message default `"assertion failed"`. Codegen emits
// `raise_assertion_error()` for `assert cond` (no message) and
// `raise_assertion_error(<msg-expr>)` for `assert cond, msg`.
[[noreturn]] inline void raise_assertion_error(std::string_view msg = "assertion failed") {
    raise<AssertionError>(msg);
}
template<typename T, typename... Rest>
[[noreturn]] inline void raise_assertion_error(std::format_string<T, Rest...> fmt, T&& arg,
                                               Rest&&... rest) {
    raise<AssertionError>(fmt, std::forward<T>(arg), std::forward<Rest>(rest)...);
}

// Per-class raise helpers (Phase 20 Stage 4b). Decouple every runtime
// throw site from `raise<E>` (which is template-instantiated against the
// concrete C++ class) so that Stage 4c can move the exception classes
// out of core.hpp without rewriting every call site. After Stage 4c
// these helpers move to `lib/tpy/tpy/_builtins/_raise.py` and the
// forward declarations relocate to throwable.hpp; their bodies become
// TPy-defined `raise X(msg)` one-liners resolved at link time.
#define TPY_DEFINE_RAISE_HELPER(name, ExceptionClass)                              \
    [[noreturn]] inline void name(std::string_view msg) {                          \
        raise<ExceptionClass>(msg);                                                \
    }                                                                              \
    template<typename T, typename... Rest>                                         \
    [[noreturn]] inline void name(std::format_string<T, Rest...> fmt, T&& arg,     \
                                  Rest&&... rest) {                                \
        raise<ExceptionClass>(fmt, std::forward<T>(arg), std::forward<Rest>(rest)...); \
    }

TPY_DEFINE_RAISE_HELPER(raise_value_error,           ValueError)
TPY_DEFINE_RAISE_HELPER(raise_type_error,            TypeError)
TPY_DEFINE_RAISE_HELPER(raise_index_error,           IndexError)
TPY_DEFINE_RAISE_HELPER(raise_key_error,             KeyError)
TPY_DEFINE_RAISE_HELPER(raise_attribute_error,       AttributeError)
TPY_DEFINE_RAISE_HELPER(raise_arithmetic_error,      ArithmeticError)
TPY_DEFINE_RAISE_HELPER(raise_zero_division_error,   ZeroDivisionError)
TPY_DEFINE_RAISE_HELPER(raise_overflow_error,        OverflowError)
TPY_DEFINE_RAISE_HELPER(raise_os_error,              OSError)
TPY_DEFINE_RAISE_HELPER(raise_file_not_found_error,  FileNotFoundError)
TPY_DEFINE_RAISE_HELPER(raise_runtime_error,         RuntimeError)
TPY_DEFINE_RAISE_HELPER(raise_not_implemented_error, NotImplementedError)
TPY_DEFINE_RAISE_HELPER(raise_memory_error,          MemoryError)

[[noreturn]] inline void raise_stop_iteration() {
    throw StopIteration{};
}

// Fixed-width integer arithmetic overflow. CPython promotes to unbounded
// BigInt and never overflows; TPy uses fixed-width storage (Int8..Int64,
// UInt8..UInt64) and panics on overflow by default. Routed through this
// helper so the policy can be made switchable later (per build / module /
// function: none / panic / throw OverflowError) without rewriting the call
// sites. Currently always panics.
[[noreturn]] inline void raise_fixedint_overflow(std::string_view msg) {
    tpy_panic(msg);
}
template<typename T, typename... Rest>
[[noreturn]] inline void raise_fixedint_overflow(std::format_string<T, Rest...> fmt,
                                                 T&& arg, Rest&&... rest) {
    tpy_panic(std::format(fmt, std::forward<T>(arg), std::forward<Rest>(rest)...));
}

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

// Strip the inline-namespace tokens libc++ and libstdc++ inject into
// demangled std:: type names (`std::__1::basic_string` /
// `std::__cxx11::basic_string`) and collapse the libstdc++ "> >" template
// closer to ">>". Without this the panic snapshots would diverge by
// host standard library.
inline void normalize_stdlib_typename(std::string& s) {
    auto strip = [&s](std::string_view marker) {
        for (size_t pos = 0; (pos = s.find(marker, pos)) != std::string::npos; ) {
            s.erase(pos, marker.size());
        }
    };
    strip("__1::");
    strip("__cxx11::");
    for (size_t pos = 0; (pos = s.find("> >", pos)) != std::string::npos; ) {
        s.erase(pos + 1, 1);
    }
}

// Demangle a std::type_info::name() result to a human-readable form
// (e.g. "tpy::BigInt" instead of "N3tpy6BigIntE"). Used by exception
// messages that name the offending C++ type. Falls back to the mangled
// name if demangling fails (typeid name is null-terminated, so the
// fallback is always usable).
inline std::string demangle_type_name(const char* mangled) {
    int status = 0;
    char* d = abi::__cxa_demangle(mangled, nullptr, nullptr, &status);
    std::string result = (status == 0 && d) ? std::string(d) : std::string(mangled);
    std::free(d);
    normalize_stdlib_typename(result);
    return result;
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
    std::string type_name = "unknown";
    const char* what_msg = nullptr;

    // Hold ex across the print: what_msg points into the exception object,
    // which is only guaranteed to outlive any active exception_ptr owning it.
    std::exception_ptr ex = std::current_exception();
    if (ex) {
        try {
            std::rethrow_exception(ex);
        } catch (const std::exception& e) {
            type_name = demangle_type_name(typeid(e).name());
            what_msg = e.what();
        } catch (...) {
        }
    }

    std::fputs("TurboPython panic: uncaught ", stderr);
    std::fwrite(type_name.data(), 1, type_name.size(), stderr);
    if (what_msg && *what_msg) {
        std::fputs(": ", stderr);
        std::fputs(what_msg, stderr);
    }
    std::fputc('\n', stderr);

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
 * Checked TypedDict optional-field access -- throws KeyError if the
 * field is absent. Used by codegen for `td["key"]` on a `total=False`
 * TypedDict. Distinct from deref_optional_check (which says "null optional
 * dereference") so it matches the existing TPy KeyError convention for
 * dict[k] misses, and avoids raw std::optional::value() whose
 * bad_optional_access::what() text diverges between libstdc++ and libc++.
 */
template <typename T>
T& typed_dict_field_check(std::optional<T>& opt) {
    if (!opt.has_value()) {
        raise_key_error("KeyError");
    }
    return *opt;
}

template <typename T>
const T& typed_dict_field_check(const std::optional<T>& opt) {
    if (!opt.has_value()) {
        raise_key_error("KeyError");
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
 * Heap-allocate a T and move-construct it from `value` (combined alloc+init).
 * The std::unique_ptr<T> overload below covers the abstract-@dynamic case
 * where the caller has already heap-allocated; it just releases the handle.
 */
template <typename T>
T* heap_take(T&& value) {
    T* p = static_cast<T*>(::operator new(sizeof(T), std::align_val_t(alignof(T))));
    ::new(static_cast<void*>(p)) T(std::move(value));
    return p;
}

template <typename T>
T* heap_take(std::unique_ptr<T> value) {
    return value.release();
}

/**
 * Destroy and free a heap-allocated T previously produced by heap_take.
 *
 * For polymorphic T (a class with a virtual destructor -- typically the
 * abstract @dynamic protocol base), uses scalar `delete p`. Per [expr.delete],
 * `delete p` with virtual destructor routes through the vtable's *deleting
 * destructor*, which knows the dynamic type and calls the matching operator
 * delete (aligned form if the dynamic type is over-aligned). This is the
 * correct disposal path -- the compiler handles alignment via the vtable.
 *
 * For non-polymorphic T, manually destroys and frees with the same alignment
 * heap_take used. Static and dynamic types coincide, so alignof(T) matches.
 */
template <typename T>
void heap_release(T* p) {
    if constexpr (std::has_virtual_destructor_v<T>) {
        delete p;  // virtual deleting destructor handles alignment + dynamic size
    } else {
        tpy::destroy_at(p);
        ::operator delete(p, std::align_val_t(alignof(T)));
    }
}

/**
 * Replace the heap-stored T at `p` with `value`, returning the live slot.
 *
 * Concrete T: same allocation, destroy + placement-new. Abstract @dynamic
 * T: the slot's size depends on the dynamic type, so reconstruction
 * requires free + reallocate -- returned pointer differs from input.
 *
 * Precondition: `value` must not alias `*p`. The abstract branch frees
 * `*p` before consuming `value`; an aliased payload would be UAF.
 * Callers must propagate the returned pointer.
 */
template <typename T>
T* heap_replace(T* p, own_param_t<T> value) {
    if constexpr (is_dyn_protocol_base_v<T>) {
        heap_release(p);
        return heap_take(std::move(value));
    } else {
        tpy::destroy_at(p);
        ::new(static_cast<void*>(p)) T(std::move(value));
        return p;
    }
}

/**
 * Convert a heap-allocated T* into an Own[T] by transferring ownership.
 *
 * Abstract @dynamic T: adopt as unique_ptr<T> (T-typed local impossible).
 * Concrete T: move out, destroy the moved-from slot, free its storage.
 */
template <typename T>
own_return_t<T> transfer_ownership(T* p) {
    if constexpr (is_dyn_protocol_base_v<T>) {
        return std::unique_ptr<T>(p);
    } else {
        T val = std::move(*p);
        tpy::destroy_at(p);
        ::operator delete(p, std::align_val_t(alignof(T)));
        return val;
    }
}

/**
 * Checked true division for floats -- throws ZeroDivisionError on a
 * zero divisor to match Python semantics.
 */
inline constexpr double truediv(double a, double b) {
    if (b == 0.0) raise_zero_division_error("float division by zero");
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
    if (b == 0.0) raise_zero_division_error("float floor division by zero");
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
    if (b == 0.0) raise_zero_division_error("float modulo");
    // Python's `%` uses floor semantics (sign-of-divisor) where C's std::fmod
    // uses truncation (sign-of-dividend). Compute the truncated remainder,
    // shift toward the divisor when the signs disagree, and on zero results
    // adopt the divisor's sign (matches CPython's float_rem in floatobject.c).
#if TPY_CMATH_CONSTEXPR
    double m = std::fmod(a, b);
#else
    double m;
    if consteval {
        long long i = static_cast<long long>(a / b);
        m = a - static_cast<double>(i) * b;
    } else {
        m = std::fmod(a, b);
    }
#endif
    if (m != 0.0) {
        if ((m < 0.0) != (b < 0.0)) m += b;
    } else {
        m = (b < 0.0) ? -0.0 : 0.0;
    }
    return m;
}

// Float32 arithmetic helpers
inline constexpr float truediv_f32(float a, float b) {
    if (b == 0.0f) raise_zero_division_error("float division by zero");
    return a / b;
}

inline constexpr float floordiv_f32(float a, float b) {
    if (b == 0.0f) raise_zero_division_error("float floor division by zero");
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
    if (b == 0.0f) raise_zero_division_error("float modulo");
    // Python's `%` uses floor semantics (sign-of-divisor); see fmod above.
#if TPY_CMATH_CONSTEXPR
    float m = std::fmod(a, b);
#else
    float m;
    if consteval {
        long long i = static_cast<long long>(a / b);
        m = a - static_cast<float>(i) * b;
    } else {
        m = std::fmod(a, b);
    }
#endif
    if (m != 0.0f) {
        if ((m < 0.0f) != (b < 0.0f)) m += b;
    } else {
        m = (b < 0.0f) ? -0.0f : 0.0f;
    }
    return m;
}

} // namespace tpy
