/**
 * TurboPython Runtime - System Utilities
 *
 * Time functions and sys.argv.
 */

#pragma once

#include <chrono>
#include <cstddef>
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
 * get_sys_argv - Return sys_argv as owned strings for module-level init.
 *
 * Called once during sys module init, after init_sys_argv() has populated sys_argv.
 */
inline std::vector<std::string> get_sys_argv() {
    return {sys_argv.begin(), sys_argv.end()};
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

} // namespace tpy
