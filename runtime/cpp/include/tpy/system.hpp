/**
 * TurboPython Runtime - System Utilities
 *
 * Time functions, sys.argv, sys.stdout / sys.stderr wrappers, and SystemExit
 * with the run_main wrapper that turns it into the exit status.
 */

#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <ostream>
#include <string>
#include <string_view>
#include <thread>
#include <variant>
#include <vector>

#include "core.hpp"
#include "union_type.hpp"

namespace tpy {

/**
 * time_time - Return seconds since epoch as double.
 *
 * Equivalent to Python's time.time().
 */
inline double time_time() {
    auto now = std::chrono::system_clock::now();
    auto duration = now.time_since_epoch();
    return std::chrono::duration<double>(duration).count();
}

/**
 * time_sleep - Suspend execution for the given number of seconds.
 *
 * Equivalent to Python's time.sleep(). With the signal layer armed, on the
 * interrupt target thread, signals arriving meanwhile are delivered inside
 * the sleep: a Ctrl-C ends it with KeyboardInterrupt, a `signal.signal`
 * handler's exception ends it too, and a handler that returns lets it sleep
 * on to the same deadline (PEP 475).
 */
inline void time_sleep(double seconds) {
    if (seconds < 0) {
        raise_value_error("sleep length must be non-negative");
    }
#ifndef TPY_NO_SIGNALS
    if (const auto* ops = interrupt_detail::ops.load(std::memory_order_acquire)) {
        if (!(seconds > 0.0)) {
            // No wait to cut short, but still a check point: `time.sleep(0)`
            // is how a busy loop stays interruptible.
            check_signals();
            return;
        }
        ops->sleep(seconds);
        return;
    }
#endif
    auto duration = std::chrono::duration<double>(seconds);
    std::this_thread::sleep_for(duration);
}

/**
 * sys_argv - Command line arguments as vector of string_view.
 *
 * Equivalent to Python's sys.argv. Initialized by init_sys_argv() in main().
 * Note: string_views point to argv strings which are valid for program lifetime.
 */
inline std::vector<std::string_view> sys_argv;

/**
 * get_sys_argv - Return sys_argv as owned strings for module-level init.
 *
 * Called once during sys module init, after init_sys_argv() has populated sys_argv.
 */
inline std::vector<std::string> get_sys_argv() {
    return {sys_argv.begin(), sys_argv.end()};
}

// Raised by sys.exit. Inherits BaseException (not Exception) like CPython, so
// `except Exception:` lets it through to run_main, which turns it into the
// process exit status after the whole stack unwound. Lives here rather than
// in core.hpp because `code` needs the union type, whose header includes
// core.hpp.
struct SystemExit : BaseException {
    using Code = ::tpy::Union<std::monostate, int32_t, std::string>;
    Code code;

    // `SystemExit()` and `SystemExit(None)` both hold code None but differ in
    // str(): '' for the former, 'None' for the latter, as in CPython.
    SystemExit() = default;
    explicit SystemExit(Code c) : BaseException(code_text(c)), code(std::move(c)) {}
    explicit SystemExit(std::monostate) : BaseException("None") {}
    explicit SystemExit(int32_t c) : SystemExit(Code(c)) {}
    explicit SystemExit(std::string_view s) : SystemExit(Code(std::string(s))) {}
    explicit SystemExit(const char* s) : SystemExit(Code(std::string(s))) {}
    explicit SystemExit(std::string s) : SystemExit(Code(std::move(s))) {}
    TPY_THROWABLE_VIRTUALS(SystemExit)

private:
    static std::string code_text(const Code& c) {
        if (const auto* i = std::get_if<int32_t>(&c)) return std::to_string(*i);
        if (const auto* s = std::get_if<std::string>(&c)) return *s;
        return "None";
    }
};

// The process exit status an uncaught SystemExit asks for. Reads `code`, not
// the message: a code reassigned before raising is the status, and a
// subclass's custom __str__ does not change it (CPython reads `.code` too). The OS keeps the low 8 bits of an int status, as CPython's.
inline int exit_status(const SystemExit& e) {
    if (const auto* i = std::get_if<int32_t>(&e.code)) return *i;
    if (const auto* s = std::get_if<std::string>(&e.code)) {
        std::fflush(stdout);
        std::fwrite(s->data(), 1, s->size(), stderr);
        std::fputc('\n', stderr);
        return 1;
    }
    return 0;
}

// The body of a standalone program's main(). A SystemExit becomes the exit
// status by RETURNING from main, not std::exit, so every frame's destructors
// (open files' buffers among them) run first and statics die after the
// message. Only SystemExit is caught: any other uncaught exception keeps the
// terminate-handler report.
inline int run_main(int argc, char* argv[], int (*entry)(int, char**)) {
    process_startup();
    try {
        return entry(argc, argv);
    } catch (const SystemExit& e) {
        return exit_status(e);
    }
}

/**
 * init_sys_argv - Initialize sys_argv from main()'s argc/argv.
 *
 * Called at program startup before __tpy_init().
 */
inline void init_sys_argv(int argc, char* argv[]) {
    sys_argv.clear();
    sys_argv.reserve(static_cast<std::size_t>(argc));
    for (int i = 0; i < argc; ++i) {
        sys_argv.emplace_back(argv[i]);
    }
}

/**
 * StdStream - non-owning wrapper around std::cout / std::cerr.
 *
 * Backs sys.stdout / sys.stderr; satisfies the Writable protocol so it works
 * as a `print(file=...)` target alongside TextFile and user types.
 */
class StdStream {
    std::ostream* sink_;

public:
    explicit StdStream(std::ostream& s) : sink_(&s) {}

    // A write can block on a full pipe; a Ctrl-C that arrived meanwhile is
    // delivered once it returns.
    int32_t write(std::string_view text) {
        sink_->write(text.data(), static_cast<std::streamsize>(text.size()));
        check_signals();
        return static_cast<int32_t>(text.size());
    }

    void flush() {
        sink_->flush();
        check_signals();
    }

    std::ostream& sink() const { return *sink_; }

    friend std::ostream& operator<<(std::ostream& os, const StdStream& s) {
        if (s.sink_ == &std::cout) return os << "<sys.stdout>";
        if (s.sink_ == &std::cerr) return os << "<sys.stderr>";
        return os << "<StdStream>";
    }
};

inline StdStream get_sys_stdout() { return StdStream(std::cout); }
inline StdStream get_sys_stderr() { return StdStream(std::cerr); }

} // namespace tpy
