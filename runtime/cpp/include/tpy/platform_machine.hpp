// `platform.machine()` for the build target: the architecture from the
// target compiler's macros, its per-OS spelling from tpy/target_platform.hpp.
// Its own header, included only by the `platform` module, so its `#error` on
// an unlisted architecture cannot stop a program that imports only `sys`.
#pragma once

#include <string_view>

#include "target_platform.hpp"

namespace tpy {

// os.uname().machine, which CPython's platform.machine() returns; it names
// the same architecture differently per OS.
#if defined(__x86_64__) || defined(_M_X64)
#if defined(TPY_PLATFORM_WIN32)
inline constexpr std::string_view platform_machine = "AMD64";
#elif defined(TPY_PLATFORM_FREEBSD)
inline constexpr std::string_view platform_machine = "amd64";
#else
inline constexpr std::string_view platform_machine = "x86_64";
#endif
#elif defined(__aarch64__) || defined(_M_ARM64)
#if defined(TPY_PLATFORM_WIN32)
inline constexpr std::string_view platform_machine = "ARM64";
#elif defined(TPY_PLATFORM_DARWIN) || defined(TPY_PLATFORM_FREEBSD)
inline constexpr std::string_view platform_machine = "arm64";
#else
inline constexpr std::string_view platform_machine = "aarch64";
#endif
#else
#error "platform.machine(): unsupported build target architecture"
#endif

}  // namespace tpy
