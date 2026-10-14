// The build target's OS, decided once from the target compiler's predefined
// macros: exactly one of TPY_PLATFORM_LINUX / _DARWIN / _WIN32 / _FREEBSD is
// defined, or none on a target TPy does not support (Android and iOS define
// __linux__ / __APPLE__ too, which is why they are tested first). The
// `__tpy__` include guards and `sys.platform` both read these, so they agree.
// No #error here: tpy.hpp includes this header for every program.
#pragma once

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

#if defined(__ANDROID__) || (defined(__APPLE__) && TARGET_OS_IPHONE)
// unsupported target: no TPY_PLATFORM_* macro
#elif defined(_WIN32)
#define TPY_PLATFORM_WIN32 1
#elif defined(__APPLE__)
#define TPY_PLATFORM_DARWIN 1
#elif defined(__linux__)
#define TPY_PLATFORM_LINUX 1
#elif defined(__FreeBSD__)
#define TPY_PLATFORM_FREEBSD 1
#endif
