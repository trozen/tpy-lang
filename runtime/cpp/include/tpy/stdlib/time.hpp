#pragma once

#include <chrono>
#include <cstdint>
#include <ctime>

namespace tpy::stdlib::time {

inline double perf_counter() {
    auto now = std::chrono::steady_clock::now();
    return std::chrono::duration<double>(now.time_since_epoch()).count();
}

inline int64_t perf_counter_ns() {
    auto now = std::chrono::steady_clock::now();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        now.time_since_epoch()).count();
}

// monotonic aliases perf_counter -- CPython ties them on POSIX, so a
// distinct clock would only diverge artificially.

inline double monotonic() {
    return perf_counter();
}

inline int64_t monotonic_ns() {
    return perf_counter_ns();
}

// system_clock can jump backward on NTP adjustments; callers measuring
// elapsed time should use monotonic_ns instead.

inline int64_t time_ns() {
    auto now = std::chrono::system_clock::now();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        now.time_since_epoch()).count();
}

// process_time: CPU time consumed by the current process, in seconds.
// CPython uses clock_gettime(CLOCK_PROCESS_CPUTIME_ID) where available;
// std::clock() is the portable C++ fallback (resolution typically ~1us
// on Linux, lower on some platforms).

inline double process_time() {
    return static_cast<double>(std::clock()) / CLOCKS_PER_SEC;
}

} // namespace tpy::stdlib::time
