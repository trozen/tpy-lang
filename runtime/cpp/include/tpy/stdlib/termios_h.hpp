#pragma once
// Hand-written facade for the `termios` stdlib module.
//
// Deliberately does NOT include <termios.h>: it `#define`s ECHO, ICANON,
// VMIN, TCSANOW, ... -- the very names the TPy `termios` module declares as
// constants, so the macros would rewrite those generated declarations. The
// real header is included only by src/stdlib/termios_impl.cpp; the helpers
// here take and return flat scalars plus the control-character bytes, so
// `struct termios` (whose layout and field widths differ between Linux and
// macOS) never crosses into a generated TU.
//
// tcgetattr_raw / tcsetattr_raw report failure by returning errno (0 on
// success) instead of throwing, so the TPy facade raises `termios.error`,
// which is not an OSError.

#include <cstdint>
#include <span>
#include <tuple>

#include "tpy/buffer_types.hpp"

namespace tpy::stdlib::termios {

// (errno, iflag, oflag, cflag, lflag, ispeed, ospeed, cc) with the NCCS raw
// control characters in `cc`: the fields CPython's tcgetattr list holds.
// On failure only the errno slot is meaningful.
std::tuple<int32_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t,
           ::tpy::ByteArray>
tcgetattr_raw(int64_t fd);

// CPython's tcsetattr: read the current attributes, overlay the given fields,
// set both speeds, apply with `when`. Returns errno, 0 on success.
int32_t tcsetattr_raw(int64_t fd, int32_t when, int64_t iflag, int64_t oflag,
                      int64_t cflag, int64_t lflag, int64_t ispeed,
                      int64_t ospeed, std::span<const uint8_t> cc);

}  // namespace tpy::stdlib::termios
