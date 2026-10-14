// The build target's identity as CPython spells it: `sys.platform` and
// `sys.byteorder` (`platform.machine()` is in tpy/platform_machine.hpp).
// Chosen by the target's TPY_PLATFORM_* macro (tpy/target_platform.hpp), so
// the generated C++ is the same whichever machine ran tpyc and a cross build
// answers for the machine it builds for. Included by the `sys` and `platform` modules through
// their `__tpy__` "includes" (not by tpy.hpp); the TPy side binds these
// through `Final[str] = native_global(...)`.
#pragma once

#include <bit>
#include <string_view>

#include "target_platform.hpp"

#define TPY_SYS_STR_(x) #x
#define TPY_SYS_STR(x) TPY_SYS_STR_(x)

namespace tpy {

#if defined(TPY_PLATFORM_WIN32)
inline constexpr std::string_view sys_platform = "win32";
#elif defined(TPY_PLATFORM_DARWIN)
inline constexpr std::string_view sys_platform = "darwin";
#elif defined(TPY_PLATFORM_LINUX)
inline constexpr std::string_view sys_platform = "linux";
#elif defined(TPY_PLATFORM_FREEBSD)
// CPython carries the major version: "freebsd14".
inline constexpr std::string_view sys_platform = "freebsd" TPY_SYS_STR(__FreeBSD__);
#elif defined(__ANDROID__)
#error "sys.platform: Android is not a supported build target yet"
#elif defined(__APPLE__)
#error "sys.platform: iOS is not a supported build target yet"
#else
#error "sys.platform: unsupported build target"
#endif

static_assert(std::endian::native == std::endian::little
                  || std::endian::native == std::endian::big,
              "sys.byteorder: mixed-endian build target");
inline constexpr std::string_view sys_byteorder =
    std::endian::native == std::endian::little ? "little" : "big";

}  // namespace tpy

#undef TPY_SYS_STR
#undef TPY_SYS_STR_
