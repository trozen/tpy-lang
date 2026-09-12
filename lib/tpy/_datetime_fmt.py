# tpy: cpp_namespace("tpystd::_datetime_fmt")
# Formatting engines for the datetime module: the strftime directive
# scanner plus the isoformat/offset/label helpers. Pure string builders
# over component values; classes live in datetime.py.
# Not for direct user import; the public surface is `datetime`.
from tpy import int32, int64
from _datetime_cal import (
    _DAY_ABBR, _DAY_FULL, _MONTH_ABBR, _MONTH_FULL,
    _ymd2ord, _days_before_year, _iso_calendar,
)


def _strftime(fmt: str, y: int32, mo: int32, d: int32, hh: int32, mm: int32,
              ss: int32, us: int32, has_tz: bool, off_us: int,
              zone: str) -> str:
    # The shared pure-TPy directive engine. Divergence-relevant facts, all
    # verified against CPython 3.12 on glibc: %Y (and %G) are NOT
    # zero-padded ('42', not '0042') although isoformat is; unknown
    # directives pass through verbatim including the '%'; a trailing lone
    # '%' is kept; %z/%Z render empty for naive values.
    o = _ymd2ord(int(y), int(mo), int(d))
    wd = int32((o + 6) % 7)          # Mon=0
    wd_sun0 = int32(o % 7)           # Sun=0
    yday0 = int32(o - _days_before_year(int(y)) - 1)
    out = ""
    i = 0
    n = len(fmt)
    while i < n:
        rel = fmt[i:].find("%")
        if rel < 0:
            out = out + fmt[i:]
            break
        j = i + rel
        out = out + fmt[i:j]
        if j + 1 >= n:
            out = out + "%"
            break
        c = fmt[j + 1]
        i = j + 2
        if c == "%":
            out = out + "%"
        elif c == "a":
            out = out + _DAY_ABBR[wd]
        elif c == "A":
            out = out + _DAY_FULL[wd]
        elif c == "b":
            out = out + _MONTH_ABBR[mo]
        elif c == "B":
            out = out + _MONTH_FULL[mo]
        elif c == "c":
            out = out + (f"{_DAY_ABBR[wd]} {_MONTH_ABBR[mo]} {d:2d} "
                         f"{hh:02d}:{mm:02d}:{ss:02d} {y}")
        elif c == "d":
            out = out + f"{d:02d}"
        elif c == "f":
            out = out + f"{us:06d}"
        elif c == "G":
            iso_y, iso_w, iso_d = _iso_calendar(int(y), int(mo), int(d))
            out = out + f"{iso_y}"
        elif c == "H":
            out = out + f"{hh:02d}"
        elif c == "I":
            h12 = hh % 12
            if h12 == 0:
                h12 = 12
            out = out + f"{h12:02d}"
        elif c == "j":
            out = out + f"{yday0 + 1:03d}"
        elif c == "m":
            out = out + f"{mo:02d}"
        elif c == "M":
            out = out + f"{mm:02d}"
        elif c == "p":
            out = out + ("AM" if hh < 12 else "PM")
        elif c == "S":
            out = out + f"{ss:02d}"
        elif c == "u":
            out = out + f"{wd + 1}"
        elif c == "U":
            out = out + f"{(yday0 + 7 - wd_sun0) // 7:02d}"
        elif c == "V":
            iso_y2, iso_w2, iso_d2 = _iso_calendar(int(y), int(mo), int(d))
            out = out + f"{int32(iso_w2):02d}"
        elif c == "w":
            out = out + f"{wd_sun0}"
        elif c == "W":
            out = out + f"{(yday0 + 7 - wd) // 7:02d}"
        elif c == "x":
            out = out + f"{mo:02d}/{d:02d}/{y % 100:02d}"
        elif c == "X":
            out = out + f"{hh:02d}:{mm:02d}:{ss:02d}"
        elif c == "y":
            out = out + f"{y % 100:02d}"
        elif c == "Y":
            out = out + f"{y}"
        elif c == "z":
            if has_tz:
                out = out + _offset_str(off_us, "")
        elif c == "Z":
            if has_tz:
                out = out + zone
        else:
            out = out + "%" + c
    return out



def _format_time(hh: int32, mm: int32, ss: int32, us: int32,
                 timespec: str = "auto") -> str:
    # 'milliseconds' truncates (never rounds) -- CPython floors the us field.
    if timespec == "auto":
        s = f"{hh:02d}:{mm:02d}:{ss:02d}"
        if us != 0:
            s = s + f".{us:06d}"
        return s
    if timespec == "hours":
        return f"{hh:02d}"
    if timespec == "minutes":
        return f"{hh:02d}:{mm:02d}"
    if timespec == "seconds":
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    if timespec == "milliseconds":
        ms = us // 1000
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"
    if timespec == "microseconds":
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{us:06d}"
    raise ValueError("Unknown timespec value")


def _offset_str(off_us: int, sep: str) -> str:
    # +HH:MM / +HHMM form; :SS only when the offset has seconds, .ffffff only
    # when it has microseconds (CPython appends both lazily in %z, isoformat
    # and the synthesized tz name alike).
    sign = "+" if off_us >= 0 else "-"
    # A valid offset is < 24h in microseconds, so int64 holds it (BigInt
    # values do not support format specs).
    a = int64(off_us if off_us >= 0 else -off_us)
    total_s, us = divmod(a, 1000000)
    hh, rem = divmod(total_s, 3600)
    mm, ss = divmod(rem, 60)
    out = f"{sign}{hh:02d}{sep}{mm:02d}"
    if ss != 0 or us != 0:
        out = out + f"{sep}{ss:02d}"
        if us != 0:
            out = out + f".{us:06d}"
    return out


def _tz_label(off_us: int) -> str:
    # CPython timezone._name_from_offset: the synthesized name for an
    # unnamed fixed offset.
    if off_us == 0:
        return "UTC"
    return "UTC" + _offset_str(off_us, ":")

