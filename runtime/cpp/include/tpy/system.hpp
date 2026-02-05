/**
 * TurboPython Runtime - System Utilities
 *
 * Global variable wrapper, time functions, and sys.argv.
 */

#pragma once

#include <chrono>
#include <cstddef>
#include <optional>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "core.hpp"

namespace tpy {

/**
 * Global<T> - Wrapper for module-level global variables.
 *
 * Defers construction of the wrapped value until assignment in the module
 * init function, ensuring proper Python-like execution order.
 */
template<typename T>
class Global {
    std::optional<T> value_;
public:
    Global() = default;

    // Disable copy/move to avoid ambiguity with T assignment
    Global(const Global&) = delete;
    Global(Global&&) = delete;
    Global& operator=(const Global&) = delete;
    Global& operator=(Global&&) = delete;

    Global& operator=(T v) {
        value_ = std::move(v);
        return *this;
    }

    operator T&() { check_init(); return *value_; }
    operator const T&() const { check_init(); return *value_; }

    T* operator->() { check_init(); return &*value_; }
    const T* operator->() const { check_init(); return &*value_; }

    T& operator*() { check_init(); return *value_; }
    const T& operator*() const { check_init(); return *value_; }

private:
    void check_init() const {
        if (!value_.has_value()) {
            tpy_panic("use of uninitialized global variable");
        }
    }
};

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
 * Equivalent to Python's time.sleep().
 */
inline void time_sleep(double seconds) {
    if (seconds < 0) {
        tpy_panic("sleep length must be non-negative");
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

} // namespace tpy
