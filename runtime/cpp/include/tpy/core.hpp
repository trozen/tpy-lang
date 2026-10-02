/**
 * TurboPython Runtime - Core Utilities
 *
 * Panic handling and pointer operations.
 */

#pragma once

#include <cerrno>
#include <cmath>
#include <concepts>
#include <csignal>
#include <cstdint>
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

#include "interrupt.hpp"
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
// PEP 3151: the OSError subclass CPython's OSError.__new__ constructs for
// an errno. The decision is made at CONSTRUCTION time (like CPython's
// __new__) and stored on the object; TPy's value model can't change the
// constructed static type, so the stored kind is applied when the object
// is RAISED (OSError::__raise__ below throws the mapped subclass).
enum class OSErrorSubclass : uint8_t {
    none, blocking_io, broken_pipe, connection_aborted, connection_refused,
    connection_reset, file_exists, file_not_found, is_a_directory,
    not_a_directory, permission, timeout,
};

inline OSErrorSubclass os_error_subclass_for(int32_t err) {
    switch (err) {
        case EAGAIN: case EALREADY: case EINPROGRESS:
#if EWOULDBLOCK != EAGAIN
        case EWOULDBLOCK:
#endif
            return OSErrorSubclass::blocking_io;
        case EPIPE: case ESHUTDOWN: return OSErrorSubclass::broken_pipe;
        case ECONNABORTED: return OSErrorSubclass::connection_aborted;
        case ECONNREFUSED: return OSErrorSubclass::connection_refused;
        case ECONNRESET:   return OSErrorSubclass::connection_reset;
        case EEXIST:  return OSErrorSubclass::file_exists;
        case ENOENT:  return OSErrorSubclass::file_not_found;
        case EISDIR:  return OSErrorSubclass::is_a_directory;
        case ENOTDIR: return OSErrorSubclass::not_a_directory;
        case EACCES: case EPERM: return OSErrorSubclass::permission;
        case ETIMEDOUT: return OSErrorSubclass::timeout;
        default: return OSErrorSubclass::none;
    }
}

// Carries CPython's structured `.errno` / `.strerror` / `.filename` OSError
// attributes. The C++ members need different names where `errno` is a C
// macro. TPy-level access maps through native_field renames in
// _builtins/_exceptions.py. Unset defaults are 0 / "" (CPython uses None;
// TPy has no Optional here).
//
// The errno-taking ctors format `message` to CPython's exact str(e) at
// construction time ("[Errno N] strerror[: 'filename'[ -> 'filename2']]");
// a post-hoc attribute assignment does NOT reformat, unlike CPython's
// attribute-driven __str__ (declared divergence in LANGUAGE_FEATURES).
// The 4-arg form (filename2, CPython's 5-arg ctor minus winerror) exists
// for the C++ rename/link raise sites and is not exposed at the TPy level.
struct OSError : Exception {
    using Exception::Exception;
    OSError(int32_t err, std::string_view strerror_arg)
        : Exception(std::format("[Errno {}] {}", err, strerror_arg)),
          error_number(err), strerror_text(strerror_arg),
          mapped_kind(os_error_subclass_for(err)) {}
    OSError(int32_t err, std::string_view strerror_arg, std::string_view filename_arg)
        : Exception(std::format("[Errno {}] {}: '{}'", err, strerror_arg, filename_arg)),
          error_number(err), strerror_text(strerror_arg), filename(filename_arg),
          mapped_kind(os_error_subclass_for(err)) {}
    OSError(int32_t err, std::string_view strerror_arg, std::string_view filename_arg,
            std::string_view filename2_arg)
        : Exception(std::format("[Errno {}] {}: '{}' -> '{}'", err, strerror_arg,
                                filename_arg, filename2_arg)),
          error_number(err), strerror_text(strerror_arg), filename(filename_arg),
          filename2(filename2_arg), mapped_kind(os_error_subclass_for(err)) {}
    int32_t error_number = 0;
    std::string strerror_text;
    std::string filename;
    std::string filename2;
    // Set only by the errno-taking ctors (message-only construction and
    // post-hoc attribute assignment leave it none, like CPython where the
    // subclass choice happens in __new__ and never re-evaluates).
    OSErrorSubclass mapped_kind = OSErrorSubclass::none;
    [[nodiscard]] std::unique_ptr<::tpy::Throwable> clone() const override {
        return std::make_unique<OSError>(*this);
    }
    // Defined after the subclasses (it throws them); this override is what
    // makes `raise` of an errno-form OSError surface the mapped subclass.
    [[noreturn]] void __raise__() const override;
};
struct FileNotFoundError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(FileNotFoundError) };
struct PermissionError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(PermissionError) };
struct BlockingIOError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(BlockingIOError) };
struct FileExistsError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(FileExistsError) };
struct NotADirectoryError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(NotADirectoryError) };
struct IsADirectoryError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(IsADirectoryError) };
struct ConnectionError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(ConnectionError) };
struct BrokenPipeError : ConnectionError { using ConnectionError::ConnectionError; TPY_THROWABLE_VIRTUALS(BrokenPipeError) };
struct ConnectionResetError : ConnectionError { using ConnectionError::ConnectionError; TPY_THROWABLE_VIRTUALS(ConnectionResetError) };
struct ConnectionRefusedError : ConnectionError { using ConnectionError::ConnectionError; TPY_THROWABLE_VIRTUALS(ConnectionRefusedError) };
struct ConnectionAbortedError : ConnectionError { using ConnectionError::ConnectionError; TPY_THROWABLE_VIRTUALS(ConnectionAbortedError) };
struct AttributeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(AttributeError) };
struct AssertionError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(AssertionError) };
struct LookupError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(LookupError) };
struct IndexError : LookupError { using LookupError::LookupError; TPY_THROWABLE_VIRTUALS(IndexError) };
struct KeyError : LookupError { using LookupError::LookupError; TPY_THROWABLE_VIRTUALS(KeyError) };
struct ArithmeticError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(ArithmeticError) };
struct ZeroDivisionError : ArithmeticError { using ArithmeticError::ArithmeticError; TPY_THROWABLE_VIRTUALS(ZeroDivisionError) };
struct OverflowError : ArithmeticError { using ArithmeticError::ArithmeticError; TPY_THROWABLE_VIRTUALS(OverflowError) };
struct FloatingPointError : ArithmeticError { using ArithmeticError::ArithmeticError; TPY_THROWABLE_VIRTUALS(FloatingPointError) };
struct TypeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(TypeError) };
struct NotImplementedError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(NotImplementedError) };
struct RuntimeError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(RuntimeError) };
struct RecursionError : RuntimeError { using RuntimeError::RuntimeError; TPY_THROWABLE_VIRTUALS(RecursionError) };
struct EOFError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(EOFError) };
struct MemoryError : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(MemoryError) };
// The C++ base of a user `ReturnException` class. Such a class is a plain
// value carried in the error slot of a std::expected and never thrown, so it
// stays outside the Throwable hierarchy: no vtable, no clone()/__raise__(), and
// no `message` of its own -- an empty base keeps a class that declares nothing
// as cheap as StopIteration. A class that declares `message` gets a generated
// `__str__` that hides this one.
struct ReturnException {
    std::string_view __str__() const { return {}; }
};

// The builtin return-only exception: it travels in the error slot of `__next__`'s
// std::expected and is never thrown, so it is a plain value outside the
// Throwable hierarchy. Empty and trivially copyable keeps every step result
// register-sized and the end-of-iteration return free of construct/destroy
// code, which is what lets a consumer loop inline `__next__`.
struct StopIteration {
    std::string_view __str__() const { return {}; }
    friend std::ostream& operator<<(std::ostream& os, const StopIteration&) { return os; }
};
struct StopAsyncIteration : Exception { using Exception::Exception; TPY_THROWABLE_VIRTUALS(StopAsyncIteration) };
struct TimeoutError : OSError { using OSError::OSError; TPY_THROWABLE_VIRTUALS(TimeoutError) };
// Inherits BaseException (not Exception) like CPython, so `except
// Exception:` does not swallow a generator close. Constructed by the
// abandonment-cleanup destructor of resumable frames and passed to
// with.__exit__ as exc_val.
struct GeneratorExit : BaseException {
    GeneratorExit() : BaseException("GeneratorExit") {}
    using BaseException::BaseException;
    TPY_THROWABLE_VIRTUALS(GeneratorExit)
};
// Inherits BaseException (not Exception) like CPython, so `except Exception:`
// does not swallow a Ctrl-C. Raised on the interrupt target thread at the next
// interruptible operation after a SIGINT (check_signals() below), and by
// asyncio.run after a SIGINT graceful shutdown.
struct KeyboardInterrupt : BaseException {
    using BaseException::BaseException;
    TPY_THROWABLE_VIRTUALS(KeyboardInterrupt)
};

// True while the process-wide SIGINT layer is armed (see interrupt.hpp).
inline bool interrupt_armed() noexcept {
    return interrupt_detail::ops.load(std::memory_order_acquire) != nullptr;
}

[[gnu::cold, gnu::noinline]] inline void deliver_interrupt() {
    const interrupt_detail::Ops* ops =
        interrupt_detail::ops.load(std::memory_order_acquire);
    if (ops != nullptr && ops->take() != 0) {
        throw KeyboardInterrupt();
    }
}

// The point where pending signals are acted on (CPython's PyErr_CheckSignals):
// today that is SIGINT's default handler, raising KeyboardInterrupt when a
// Ctrl-C is pending for this thread; user `signal.signal` handlers would run
// here too. Called after operations that cannot wait on the wake fd themselves
// (stdout / file I/O, raise_signal); one relaxed load when nothing is pending.
inline void check_signals() {
    if (interrupt_detail::pending.load(std::memory_order_relaxed) != 0) [[unlikely]] {
        deliver_interrupt();
    }
}

// The same check as a stream manipulator, the last token of every generated
// print chain (`std::cout << x << "\n" << ::tpy::check_signals;`): a Ctrl-C
// that arrived while the line was written is raised once it is out, like the
// sys.stdout.write check point. The standard inserter calls a manipulator
// directly, so the throw is not caught into badbit.
inline std::ostream& check_signals(std::ostream& os) {
    check_signals();
    return os;
}

// Embedding API for --no-main builds, whose host owns signal dispositions.
// install_interrupt_handler() arms the SIGINT layer with the calling thread as
// the interrupt target (what generated main() does for a standalone program)
// and installs its SIGINT handler; returns false on failure. A host that keeps
// its own SIGINT handler passes false and calls request_interrupt() from that
// handler (async-signal-safe). Either way the Ctrl-C reaches the host as a
// tpy::KeyboardInterrupt thrown out of the TPy call that was running.
inline bool install_interrupt_handler(bool install_sigint_handler = true) {
    return tpy_interrupt_install(install_sigint_handler ? 1 : 0) == 0;
}

inline void request_interrupt() noexcept {
    tpy_request_interrupt();
}

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
// Structured OSError form: picks the ctor arity by which optional parts are
// present, so the message never carries an empty ": ''" segment ("" is TPy's
// stand-in for CPython's None filename).
template<typename E>
    requires std::derived_from<E, OSError>
[[noreturn]] inline void raise(int32_t err, std::string_view strerror_arg,
                               std::string_view filename_arg = "",
                               std::string_view filename2_arg = "") {
    if (!filename2_arg.empty()) throw E(err, strerror_arg, filename_arg, filename2_arg);
    if (!filename_arg.empty()) throw E(err, strerror_arg, filename_arg);
    throw E(err, strerror_arg);
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

// OSError-family helpers additionally accept CPython's structured
// (errno, strerror[, filename[, filename2]]) form; the OSError ctor formats
// the message exactly like CPython's str(e). Message-only forms remain for
// non-syscall raises (e.g. mode errors) that carry no errno.
#define TPY_DEFINE_RAISE_OS_HELPER(name, ExceptionClass)                            \
    TPY_DEFINE_RAISE_HELPER(name, ExceptionClass)                                   \
    [[noreturn]] inline void name(int32_t err, std::string_view strerror_arg,       \
                                  std::string_view filename_arg = "",               \
                                  std::string_view filename2_arg = "") {            \
        raise<ExceptionClass>(err, strerror_arg, filename_arg, filename2_arg);      \
    }

TPY_DEFINE_RAISE_HELPER(raise_value_error,           ValueError)
TPY_DEFINE_RAISE_HELPER(raise_type_error,            TypeError)
TPY_DEFINE_RAISE_HELPER(raise_index_error,           IndexError)
TPY_DEFINE_RAISE_HELPER(raise_key_error,             KeyError)
TPY_DEFINE_RAISE_HELPER(raise_attribute_error,       AttributeError)
TPY_DEFINE_RAISE_HELPER(raise_arithmetic_error,      ArithmeticError)
TPY_DEFINE_RAISE_HELPER(raise_zero_division_error,   ZeroDivisionError)
TPY_DEFINE_RAISE_HELPER(raise_overflow_error,        OverflowError)
TPY_DEFINE_RAISE_OS_HELPER(raise_os_error,              OSError)
TPY_DEFINE_RAISE_OS_HELPER(raise_file_not_found_error,  FileNotFoundError)
TPY_DEFINE_RAISE_OS_HELPER(raise_permission_error,      PermissionError)
TPY_DEFINE_RAISE_OS_HELPER(raise_file_exists_error,     FileExistsError)
TPY_DEFINE_RAISE_OS_HELPER(raise_not_a_directory_error, NotADirectoryError)
TPY_DEFINE_RAISE_OS_HELPER(raise_is_a_directory_error,  IsADirectoryError)
TPY_DEFINE_RAISE_OS_HELPER(raise_blocking_io_error,     BlockingIOError)
TPY_DEFINE_RAISE_HELPER(raise_runtime_error,         RuntimeError)
TPY_DEFINE_RAISE_HELPER(raise_not_implemented_error, NotImplementedError)
TPY_DEFINE_RAISE_HELPER(raise_memory_error,          MemoryError)
TPY_DEFINE_RAISE_HELPER(raise_eof_error,             EOFError)

// Throw the OSError subclass for a pre-computed mapping kind. The shared
// tail of raise_mapped_os_error (kind from a live errno) and
// OSError::__raise__ (kind stored at construction time).
[[noreturn]] inline void throw_os_error_as(OSErrorSubclass kind, int32_t err,
                                           std::string_view strerror_arg,
                                           std::string_view filename_arg = "",
                                           std::string_view filename2_arg = "") {
    switch (kind) {
        case OSErrorSubclass::blocking_io:
            raise<BlockingIOError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::broken_pipe:
            raise<BrokenPipeError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::connection_aborted:
            raise<ConnectionAbortedError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::connection_refused:
            raise<ConnectionRefusedError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::connection_reset:
            raise<ConnectionResetError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::file_exists:
            raise<FileExistsError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::file_not_found:
            raise<FileNotFoundError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::is_a_directory:
            raise<IsADirectoryError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::not_a_directory:
            raise<NotADirectoryError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::permission:
            raise<PermissionError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::timeout:
            raise<TimeoutError>(err, strerror_arg, filename_arg, filename2_arg);
        case OSErrorSubclass::none:
        default:
            raise<OSError>(err, strerror_arg, filename_arg, filename2_arg);
    }
}

// PEP 3151: map an errno to the OSError subclass CPython's OSError.__new__
// constructs for it. Shared by the os-module raise sites (os_impl.cpp) and
// the file.hpp open paths so both stay on one table. Errnos whose CPython
// class TPy doesn't define (EINTR/ECHILD/ESRCH -> InterruptedError/
// ChildProcessError/ProcessLookupError) fall through to plain OSError.
[[noreturn]] inline void raise_mapped_os_error(int32_t err, std::string_view strerror_arg,
                                               std::string_view filename_arg = "",
                                               std::string_view filename2_arg = "") {
    // A syscall only fails with EINTR when a signal handler ran without
    // SA_RESTART semantics for it; when that was a Ctrl-C, CPython surfaces
    // KeyboardInterrupt rather than the OSError.
    if (err == EINTR) {
        check_signals();
    }
    throw_os_error_as(os_error_subclass_for(err), err, strerror_arg,
                      filename_arg, filename2_arg);
}

// Raising an errno-form OSError value surfaces the subclass CPython's
// __new__ would have constructed: the kind was fixed at ctor time, the
// attribute values travel as they are at raise time (both matching
// CPython, where the instance -- with any post-hoc attribute mutations --
// is what propagates). Message-only constructions (kind none) throw as
// plain OSError. `throw *this` would slice the mapping away, which is why
// this class does not use TPY_THROWABLE_VIRTUALS.
// Lifetime: throw_os_error_as reads string_views into *this's own string
// members; that is safe because [except.throw] fully constructs the thrown
// object (copying the viewed chars into its own strings) BEFORE unwinding
// -- and hence *this's destruction -- can begin. Keep the construction
// eager; deferring it past the throw expression would dangle.
inline void OSError::__raise__() const {
    if (mapped_kind == OSErrorSubclass::none) throw *this;
    throw_os_error_as(mapped_kind, error_number, strerror_text, filename, filename2);
}

// Fixed-width integer arithmetic overflow. CPython promotes to unbounded
// BigInt and never overflows; TPy uses fixed-width storage (int8..int64,
// uint8..uint64) and panics on overflow by default. Routed through this
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
            // CPython reports an uncaught KeyboardInterrupt tersely and then
            // dies by SIGINT itself, so a parent shell sees the 130 / signal
            // status an unhandled Ctrl-C produces rather than a crash. Only
            // the exact type: a subclass is reported like any exception, as
            // CPython's exit-by-SIGINT also tests the exact type.
            if (typeid(e) == typeid(KeyboardInterrupt)) {
                const auto& ki = static_cast<const KeyboardInterrupt&>(e);
                std::fflush(stdout);
                std::fputs("KeyboardInterrupt", stderr);
                if (!ki.message.empty()) {
                    std::fputs(": ", stderr);
                    std::fwrite(ki.message.data(), 1, ki.message.size(), stderr);
                }
                std::fputc('\n', stderr);
                tpy_interrupt_exit_by_sigint();
            }
            type_name = demangle_type_name(typeid(e).name());
            what_msg = e.what();
        } catch (...) {
        }
    }

    // Flush any pending pre-panic output (prints from finally bodies,
    // partial lines, etc.) so users see the load-bearing side effects
    // that ran before the panic. `_Exit` below bypasses all stream
    // flushing, so without this the user sees only the panic message.
    // `std::cout` is sync'd with C's stdout by default, so fflush
    // alone covers both streams (avoids dragging in `<iostream>`).
    std::fflush(stdout);

    std::fputs("TurboPython panic: uncaught ", stderr);
    std::fwrite(type_name.data(), 1, type_name.size(), stderr);
    if (what_msg && *what_msg) {
        std::fputs(": ", stderr);
        std::fputs(what_msg, stderr);
    }
    std::fputc('\n', stderr);

    std::_Exit(1);
}

// Reports an exception that escaped a `__del__` body, then terminates the
// process (fail-fast). A C++ destructor is noexcept, so the exception cannot
// propagate; rather than swallow it (which hides an incomplete-cleanup bug)
// TPy treats a throwing destructor as fatal -- matching C++'s own
// noexcept-destructor rule (Rust aborts only on a panic inside Drop while a
// panic is already unwinding), not CPython's print-and-continue. stdout is
// flushed first so buffered program output
// orders before the report (mirrors tpy_terminate_handler).
[[noreturn]] inline void report_del_exception(const std::exception& e) noexcept {
    std::string type_name = demangle_type_name(typeid(e).name());
    std::fflush(stdout);
    std::fputs("TurboPython panic: uncaught exception in __del__: ", stderr);
    std::fwrite(type_name.data(), 1, type_name.size(), stderr);
    const char* what_msg = e.what();
    if (what_msg && *what_msg) {
        std::fputs(": ", stderr);
        std::fputs(what_msg, stderr);
    }
    std::fputc('\n', stderr);
    std::_Exit(1);
}

[[noreturn]] inline void report_del_exception() noexcept {
    std::fflush(stdout);
    std::fputs("TurboPython panic: uncaught exception in __del__\n", stderr);
    std::_Exit(1);
}

// Process-global dispositions a standalone TPy program installs when it owns
// the OS process; grouped here so future once-per-process setup lands in the
// runtime, not in generated main(). Not emitted for --no-main / ext_module
// builds, where the CPython host owns signal/terminate disposition.
inline void process_startup() {
    std::set_terminate(&tpy_terminate_handler);
    // Match CPython, which ignores SIGPIPE at interpreter init: a write to a
    // hung-up peer then returns EPIPE (a catchable OSError) instead of the
    // kernel's default SIGPIPE termination.
    std::signal(SIGPIPE, SIG_IGN);
    // Ctrl-C -> KeyboardInterrupt on this (the main) thread, unless SIGINT was
    // inherited as ignored, which CPython also leaves alone.
    tpy_interrupt_process_startup();
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
 * Borrow an owned string as a null-terminated C string.
 *
 * A free function rather than a spliced `.c_str()`: the parameter applies
 * the standard conversion, so a string literal or a `string_view`-formed
 * argument materializes a proper `std::string` bound to the parameter
 * instead of producing `"lit".c_str()`, which does not compile.
 *
 * The result borrows `s`'s buffer, so it outlives the call only if `s`
 * does -- an argument converted at the call site dies with the
 * full-expression.
 *
 * The cast is well-defined because uint8_t is unsigned char on every
 * platform in scope, which the aliasing rules exempt; it would be UB on
 * one where uint8_t is an extended integer type instead.
 */
inline const uint8_t* cstr(const std::string& s) noexcept {
    return reinterpret_cast<const uint8_t*>(s.c_str());
}

/**
 * Heap-allocate a T and move-construct it from `value` (combined alloc+init).
 * The std::unique_ptr<T> overload below covers the abstract-@dynamic case
 * where the caller has already heap-allocated; it just releases the handle.
 *
 * Must stay `new T` (not a hand-rolled `::operator new(sizeof, align_val_t)`):
 * the new-expression picks the aligned operator new iff T is over-aligned, the
 * SAME rule heap_release's `delete p` and a @dynamic base's virtual deleting
 * destructor use -- so alloc and free always pair. Forcing aligned-new
 * unconditionally desynchronized that pair for alignof(T) <= the default new
 * alignment (aligned new vs plain delete -> new-delete-type-mismatch UB).
 */
template <typename T>
T* heap_take(T&& value) {
    return new T(std::move(value));
}

template <typename T>
T* heap_take(std::unique_ptr<T> value) {
    return value.release();
}

/**
 * Destroy and free a heap-allocated T previously produced by heap_take.
 *
 * Mirror of heap_take's `new T`: scalar `delete p` selects the operator
 * delete that pairs with the operator new the matching `new`-expression
 * used (aligned form only when alignof(T) exceeds the default new
 * alignment). For polymorphic T the virtual deleting destructor routes
 * the same choice through the vtable for the dynamic type. Hand-rolling
 * an unconditional aligned `::operator delete` here would mismatch the
 * non-aligned delete the compiler bakes into a derived type's deleting
 * destructor (UB; ASan flags new-delete-type-mismatch) -- a `_RcCell`
 * allocated by heap_take then freed through its @dynamic base hit exactly
 * that. `delete p` keeps allocation and disposal in lockstep.
 */
template <typename T>
void heap_release(T* p) {
    delete p;
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
        delete p;  // pairs with heap_take's `new T` (see heap_release)
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

// float32 arithmetic helpers
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
