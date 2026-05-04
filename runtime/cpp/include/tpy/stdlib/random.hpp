#pragma once

#include <cstdint>
#include <random>

namespace tpy::stdlib::random {

// Returns a uint32 sourced from the OS entropy pool. Backed by
// std::random_device, which uses /dev/urandom on Linux, getentropy /
// arc4random on BSD/macOS, and BCryptGenRandom on Windows. Each call
// returns fresh entropy; instance is held in a function-local static
// since opening the entropy source can be expensive on some platforms.
//
// Thread safety: C++11 guarantees the static initialization is
// thread-safe. Concurrent rd() calls are thread-safe in practice on
// libstdc++ / libc++ / MSVC (all read from OS primitives) but the
// language standard does not contract this. When TPy gains threading,
// callers needing multi-thread safety should serialize or construct
// per-thread Random instances.
inline uint32_t os_entropy_uint32() {
    static std::random_device rd;
    return rd();
}

} // namespace tpy::stdlib::random
