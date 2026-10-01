/**
 * TurboPython Runtime - System Utilities
 *
 * Time functions, sys.argv, and sys.stdout / sys.stderr wrappers.
 */

#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <ostream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "core.hpp"

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
 * Equivalent to Python's time.sleep(). With the SIGINT layer armed a Ctrl-C
 * ends the sleep early with KeyboardInterrupt (on the interrupt target thread).
 */
inline void time_sleep(double seconds) {
    if (seconds < 0) {
        raise_value_error("sleep length must be non-negative");
    }
    if (const auto* ops = interrupt_detail::ops.load(std::memory_order_acquire)) {
        if (!(seconds > 0.0)) {
            // No wait to cut short, but still a check point: `time.sleep(0)`
            // is how a busy loop stays interruptible.
            check_interrupt();
            return;
        }
        if (ops->sleep(seconds) == interrupt_detail::kInterrupted) {
            throw KeyboardInterrupt();
        }
        return;
    }
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

/**
 * sys_exit - Terminate the program with the given exit code.
 *
 * Equivalent to Python's sys.exit(code) for an integer argument.
 * Static destructors and atexit handlers run; thread-local
 * destructors and finally clauses do NOT (this is std::exit, not
 * a thrown SystemExit). Acceptable for a v1 binding driven by
 * argparse's --help / parse-error paths.
 */
[[noreturn]] inline void sys_exit(int code) {
    std::exit(code);
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
        check_interrupt();
        return static_cast<int32_t>(text.size());
    }

    void flush() {
        sink_->flush();
        check_interrupt();
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
