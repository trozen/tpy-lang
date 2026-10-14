// Bodies for tpy::stdlib::termios (declared in tpy/stdlib/termios_h.hpp) and
// the termios constant globals. Out of line so <termios.h> and its macros stay
// out of every TPy-generated TU.

#include <tpy/stdlib/termios_h.hpp>

#include <algorithm>
#include <cerrno>
#include <cstddef>
#include <cstdint>

#include <termios.h>

namespace tpy::stdlib::termios {

std::tuple<int32_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t,
           ::tpy::ByteArray>
tcgetattr_raw(int64_t fd) {
    struct ::termios mode{};
    if (::tcgetattr(static_cast<int>(fd), &mode) != 0)
        return {errno, 0, 0, 0, 0, 0, 0, {}};
    ::tpy::ByteArray cc;
    cc.assign(mode.c_cc, mode.c_cc + NCCS);
    return {0,
            static_cast<int64_t>(mode.c_iflag),
            static_cast<int64_t>(mode.c_oflag),
            static_cast<int64_t>(mode.c_cflag),
            static_cast<int64_t>(mode.c_lflag),
            static_cast<int64_t>(::cfgetispeed(&mode)),
            static_cast<int64_t>(::cfgetospeed(&mode)),
            std::move(cc)};
}

int32_t tcsetattr_raw(int64_t fd, int32_t when, int64_t iflag, int64_t oflag,
                      int64_t cflag, int64_t lflag, int64_t ispeed,
                      int64_t ospeed, std::span<const uint8_t> cc) {
    const int f = static_cast<int>(fd);
    // Start from the live attributes, as CPython does, so fields the TPy side
    // does not carry (c_line on Linux) keep their current values.
    struct ::termios mode{};
    if (::tcgetattr(f, &mode) != 0) return errno;
    mode.c_iflag = static_cast<tcflag_t>(iflag);
    mode.c_oflag = static_cast<tcflag_t>(oflag);
    mode.c_cflag = static_cast<tcflag_t>(cflag);
    mode.c_lflag = static_cast<tcflag_t>(lflag);
    std::copy_n(cc.begin(), std::min<std::size_t>(cc.size(), NCCS), mode.c_cc);
    if (::cfsetispeed(&mode, static_cast<speed_t>(ispeed)) != 0) return errno;
    if (::cfsetospeed(&mode, static_cast<speed_t>(ospeed)) != 0) return errno;
    if (::tcsetattr(f, when, &mode) != 0) return errno;
    return 0;
}

}  // namespace tpy::stdlib::termios

// Constant values read from <termios.h>, exposed to the TPy facade through
// `native_global(..., binding="C")`. `extern const`: the `Final`
// native_global declaration is const, and inside `extern "C" {}` a const
// would otherwise get internal linkage. The flags are int64 because
// tcflag_t is `unsigned long` on macOS, where NOFLSH is 0x80000000.
extern "C" {

extern const std::int32_t tpy_const_termios_tcsanow = TCSANOW;
extern const std::int32_t tpy_const_termios_tcsadrain = TCSADRAIN;
extern const std::int32_t tpy_const_termios_tcsaflush = TCSAFLUSH;
extern const std::int32_t tpy_const_termios_vmin = VMIN;
extern const std::int32_t tpy_const_termios_vtime = VTIME;
extern const std::int32_t tpy_const_termios_nccs = NCCS;

extern const std::int64_t tpy_const_termios_ignbrk = IGNBRK;
extern const std::int64_t tpy_const_termios_brkint = BRKINT;
extern const std::int64_t tpy_const_termios_ignpar = IGNPAR;
extern const std::int64_t tpy_const_termios_parmrk = PARMRK;
extern const std::int64_t tpy_const_termios_inpck = INPCK;
extern const std::int64_t tpy_const_termios_istrip = ISTRIP;
extern const std::int64_t tpy_const_termios_inlcr = INLCR;
extern const std::int64_t tpy_const_termios_igncr = IGNCR;
extern const std::int64_t tpy_const_termios_icrnl = ICRNL;
extern const std::int64_t tpy_const_termios_ixon = IXON;
extern const std::int64_t tpy_const_termios_ixany = IXANY;
extern const std::int64_t tpy_const_termios_ixoff = IXOFF;
extern const std::int64_t tpy_const_termios_opost = OPOST;
extern const std::int64_t tpy_const_termios_parenb = PARENB;
extern const std::int64_t tpy_const_termios_csize = CSIZE;
extern const std::int64_t tpy_const_termios_cs8 = CS8;
extern const std::int64_t tpy_const_termios_echo = ECHO;
extern const std::int64_t tpy_const_termios_echoe = ECHOE;
extern const std::int64_t tpy_const_termios_echok = ECHOK;
extern const std::int64_t tpy_const_termios_echonl = ECHONL;
extern const std::int64_t tpy_const_termios_icanon = ICANON;
extern const std::int64_t tpy_const_termios_iexten = IEXTEN;
extern const std::int64_t tpy_const_termios_isig = ISIG;
extern const std::int64_t tpy_const_termios_noflsh = NOFLSH;
extern const std::int64_t tpy_const_termios_tostop = TOSTOP;

}  // extern "C"
