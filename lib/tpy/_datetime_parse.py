# tpy: cpp_namespace("tpystd::_datetime_parse")
# Parsing engines for the datetime module: the strptime scanner (a
# hand-rolled port of CPython's _strptime.py acceptance rules) and the
# fromisoformat component parsers (3.11+ grammar, digit-strict like the
# C implementation). Everything returns component tuples -- construction
# happens in datetime.py (no circular import), which also keeps
# this module free of the class layer.
# Not for direct user import; the public surface is `datetime`.
#
# NB: tuple-unpack target names are deliberately unique per directive
# branch (vd/vm/vh...): the pre-scan hoisting misses tuple-unpack targets
# reused across sibling branches, emitting assignments to undeclared C++
# locals.
from tpy import char, int32
from _datetime_cal import (
    _DAY_ABBR, _DAY_FULL, _MONTH_ABBR, _MONTH_FULL, _MAXORDINAL,
    _ymd2ord, _ord2ymd, _is_leap, _isoweek1monday, _isoweek_to_gregorian,
)


def _is_digit_char(c: char) -> bool:
    o = ord(c)
    return 48 <= o <= 57


def _is_space_char(c: char) -> bool:
    o = ord(c)
    return o == 32 or 9 <= o <= 13


def _char_ieq(a: char, b: char) -> bool:
    # ASCII case-insensitive char compare (strptime literals and names
    # match case-insensitively -- CPython compiles with IGNORECASE).
    x = ord(a)
    y = ord(b)
    if 65 <= x <= 90:
        x = x + 32
    if 65 <= y <= 90:
        y = y + 32
    return x == y


def _take_num(data: str, p: int, max_len: int, lo: int,
              hi: int) -> tuple[int, int]:
    # Longest-first bounded numeric field, mirroring TimeRE's alternation
    # (a 2-digit try that exceeds the range falls back to 1 digit).
    # Returns (value, new_p); value -1 means no match.
    m = len(data)
    avail = 0
    while avail < max_len and p + avail < m and _is_digit_char(data[p + avail]):
        avail = avail + 1
    ln = avail
    while ln >= 1:
        v = int(data[p:p + ln])
        if lo <= v <= hi:
            return (v, p + ln)
        ln = ln - 1
    return (-1, p)


def _take_exact_num(data: str, p: int, ln: int) -> tuple[int, int]:
    # Fixed-width numeric field (%y two digits, %Y/%G four).
    m = len(data)
    k = 0
    while k < ln and p + k < m and _is_digit_char(data[p + k]):
        k = k + 1
    if k < ln:
        return (-1, p)
    return (int(data[p:p + ln]), p + ln)


def _match_name(data: str, p: int, names: list[str],
                start: int) -> tuple[int, int]:
    # Case-insensitive table match, longest candidate wins (so 'March'
    # is not cut short as 'Mar'). Returns (index, new_p); index -1 = none.
    best = -1
    best_len = 0
    i = start
    n = len(names)
    while i < n:
        cand = names[i]
        cl = len(cand)
        if cl > best_len and p + cl <= len(data):
            if data[p:p + cl].lower() == cand.lower():
                best = i
                best_len = cl
        i = i + 1
    if best < 0:
        return (-1, p)
    return (best, p + best_len)


class _ParseState:
    # Mutable scratch for one strptime run (mirrors _strptime.py locals).
    has_year: bool
    year: int
    has_iso_year: bool
    iso_year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    fraction: int
    has_hour12: bool
    hour12: int
    ampm: int            # -1 none, 0 AM, 1 PM
    weekday: int         # Mon=0; -1 none
    has_julian: bool
    julian: int
    week_of_year: int    # -1 none
    week_starts_mon: bool
    has_iso_week: bool
    iso_week: int
    has_gmtoff: bool
    gmtoff: int          # seconds
    gmtoff_fraction: int # microseconds
    has_zname: bool
    zname: str

    def __init__(self) -> None:
        self.has_year = False
        self.year = 0
        self.has_iso_year = False
        self.iso_year = 0
        self.month = 1
        self.day = 1
        self.hour = 0
        self.minute = 0
        self.second = 0
        self.fraction = 0
        self.has_hour12 = False
        self.hour12 = 0
        self.ampm = -1
        self.weekday = -1
        self.has_julian = False
        self.julian = 0
        self.week_of_year = -1
        self.week_starts_mon = False
        self.has_iso_week = False
        self.iso_week = 0
        self.has_gmtoff = False
        self.gmtoff = 0
        self.gmtoff_fraction = 0
        self.has_zname = False
        self.zname = ""


def _parse_fail(data: str, fmt: str) -> None:
    raise ValueError(
        f"time data '{data}' does not match format '{fmt}'")


def _parse_zoffset(data: str, p: int, st: _ParseState, fmt: str) -> int:
    # %z: Z (case-sensitive) | +HH[:]MM[[:]SS[.ffffff]] with consistent
    # colon use (CPython post-checks the regex match; both paths raise
    # ValueError on inconsistency).
    m = len(data)
    if p < m and data[p] == "Z":
        st.has_gmtoff = True
        st.gmtoff = 0
        st.gmtoff_fraction = 0
        return p + 1
    if p >= m or (data[p] != "+" and data[p] != "-"):
        _parse_fail(data, fmt)
    negative = data[p] == "-"
    p = p + 1
    hh, p2 = _take_exact_num(data, p, 2)
    if hh < 0:
        _parse_fail(data, fmt)
    p = p2
    colon = p < m and data[p] == ":"
    if colon:
        p = p + 1
    mm, p2 = _take_exact_num(data, p, 2)
    if mm < 0 or mm > 59:
        _parse_fail(data, fmt)
    p = p2
    ss = 0
    has_ss = False
    if p < m:
        if data[p] == ":":
            if not colon:
                raise ValueError(f"Inconsistent use of : in {data}")
            sv, sp = _take_exact_num(data, p + 1, 2)
            if 0 <= sv <= 59:
                ss = sv
                has_ss = True
                p = sp
        elif (not colon and _is_digit_char(data[p]) and p + 1 < m
                and _is_digit_char(data[p + 1])):
            v2 = int(data[p:p + 2])
            if v2 <= 59:
                ss = v2
                has_ss = True
                p = p + 2
        elif colon and _is_digit_char(data[p]):
            # '+05:3015': seconds without the second colon after a
            # colon-form offset.
            raise ValueError(f"Inconsistent use of : in {data}")
    frac = 0
    if has_ss and p < m and data[p] == ".":
        # CPython's regex nests the fraction inside the seconds group.
        q = p + 1
        k = 0
        while q + k < m and k < 6 and _is_digit_char(data[q + k]):
            k = k + 1
        if k < 1:
            _parse_fail(data, fmt)
        frac = int(data[q:q + k]) * (10 ** (6 - k))
        p = q + k
    off = hh * 3600 + mm * 60 + ss
    if negative:
        off = -off
        frac = -frac
    st.has_gmtoff = True
    st.gmtoff = off
    st.gmtoff_fraction = frac
    return p


def _apply_directive(d: char, data: str, p: int, st: _ParseState,
                     fmt: str) -> int:
    m = len(data)
    if d == "%":
        if p >= m or data[p] != "%":
            _parse_fail(data, fmt)
        return p + 1
    if d == "d":
        v, p2 = _take_num(data, p, 2, 1, 31)
        if v < 0:
            # The ANSI-C %c form: a space-padded single digit (' 5').
            if (p + 1 < m and data[p] == " "
                    and _is_digit_char(data[p + 1])
                    and data[p + 1] != "0"):
                st.day = int(data[p + 1:p + 2])
                return p + 2
            _parse_fail(data, fmt)
        st.day = v
        return p2
    if d == "m":
        vm, pm2 = _take_num(data, p, 2, 1, 12)
        if vm < 0:
            _parse_fail(data, fmt)
        st.month = vm
        return pm2
    if d == "H":
        vh, ph2 = _take_num(data, p, 2, 0, 23)
        if vh < 0:
            _parse_fail(data, fmt)
        st.hour = vh
        return ph2
    if d == "I":
        vi, pi2 = _take_num(data, p, 2, 1, 12)
        if vi < 0:
            _parse_fail(data, fmt)
        st.has_hour12 = True
        st.hour12 = vi
        return pi2
    if d == "M":
        vmin, pmin2 = _take_num(data, p, 2, 0, 59)
        if vmin < 0:
            _parse_fail(data, fmt)
        st.minute = vmin
        return pmin2
    if d == "S":
        vs, ps2 = _take_num(data, p, 2, 0, 61)
        if vs < 0:
            _parse_fail(data, fmt)
        st.second = vs
        return ps2
    if d == "y":
        vy, py2 = _take_exact_num(data, p, 2)
        if vy < 0:
            _parse_fail(data, fmt)
        # Open Group pivot: 00-68 -> 2000s, 69-99 -> 1900s.
        st.has_year = True
        st.year = vy + 2000 if vy <= 68 else vy + 1900
        return py2
    if d == "Y":
        vyy, pyy2 = _take_exact_num(data, p, 4)
        if vyy < 0:
            _parse_fail(data, fmt)
        st.has_year = True
        st.year = vyy
        return pyy2
    if d == "G":
        vg, pg2 = _take_exact_num(data, p, 4)
        if vg < 0:
            _parse_fail(data, fmt)
        st.has_iso_year = True
        st.iso_year = vg
        return pg2
    if d == "j":
        vj, pj2 = _take_num(data, p, 3, 1, 366)
        if vj < 0:
            _parse_fail(data, fmt)
        st.has_julian = True
        st.julian = vj
        return pj2
    if d == "U" or d == "W":
        vw, pw2 = _take_num(data, p, 2, 0, 53)
        if vw < 0:
            _parse_fail(data, fmt)
        st.week_of_year = vw
        st.week_starts_mon = d == "W"
        return pw2
    if d == "V":
        vv, pv2 = _take_num(data, p, 2, 1, 53)
        if vv < 0:
            _parse_fail(data, fmt)
        st.has_iso_week = True
        st.iso_week = vv
        return pv2
    if d == "w":
        vwd, pwd2 = _take_num(data, p, 1, 0, 6)
        if vwd < 0:
            _parse_fail(data, fmt)
        st.weekday = 6 if vwd == 0 else vwd - 1
        return pwd2
    if d == "u":
        vu, pu2 = _take_num(data, p, 1, 1, 7)
        if vu < 0:
            _parse_fail(data, fmt)
        st.weekday = vu - 1
        return pu2
    if d == "f":
        k = 0
        while p + k < m and k < 6 and _is_digit_char(data[p + k]):
            k = k + 1
        if k < 1:
            _parse_fail(data, fmt)
        st.fraction = int(data[p:p + k]) * (10 ** (6 - k))
        return p + k
    if d == "a":
        ida, nda = _match_name(data, p, _DAY_ABBR, 0)
        if ida < 0:
            _parse_fail(data, fmt)
        st.weekday = ida
        return nda
    if d == "A":
        idA, ndA = _match_name(data, p, _DAY_FULL, 0)
        if idA < 0:
            _parse_fail(data, fmt)
        st.weekday = idA
        return ndA
    if d == "b":
        imb, nmb = _match_name(data, p, _MONTH_ABBR, 1)
        if imb < 0:
            _parse_fail(data, fmt)
        st.month = imb
        return nmb
    if d == "B":
        imB, nmB = _match_name(data, p, _MONTH_FULL, 1)
        if imB < 0:
            _parse_fail(data, fmt)
        st.month = imB
        return nmB
    if d == "p":
        iap, nap = _match_name(data, p, _AM_PM, 0)
        if iap < 0:
            _parse_fail(data, fmt)
        st.ampm = iap
        return nap
    if d == "z":
        return _parse_zoffset(data, p, st, fmt)
    if d == "Z":
        # Stricter than CPython, which also accepts the host's live
        # time.tzname pair (host-dependent); TPy pins the portable set.
        izn, nzn = _match_name(data, p, _ZNAMES, 0)
        if izn < 0:
            _parse_fail(data, fmt)
        st.has_zname = True
        st.zname = data[p:nzn]
        return nzn
    raise ValueError(f"'{d}' is a bad directive in format '{fmt}'")


_AM_PM: list[str] = ["AM", "PM"]
_ZNAMES: list[str] = ["UTC", "GMT"]


def _calc_julian_from_week(year: int, week_of_year: int, day_of_week: int,
                           week_starts_mon: bool) -> int:
    # Port of _strptime._calc_julian_from_U_or_W.
    first_weekday = (_ymd2ord(year, 1, 1) + 6) % 7
    if not week_starts_mon:
        first_weekday = (first_weekday + 1) % 7
        day_of_week = (day_of_week + 1) % 7
    week_0_length = (7 - first_weekday) % 7
    if week_of_year == 0:
        return 1 + day_of_week - first_weekday
    days_to_week = week_0_length + 7 * (week_of_year - 1)
    return 1 + days_to_week + day_of_week


def _strptime_impl(
        data: str, fmt: str
) -> tuple[int, int, int, int, int, int, int, bool, int, str]:
    # %c/%x/%X expand to their C-locale directive compositions up front
    # (their sub-directives carry no further expansion).
    if "%c" in fmt or "%x" in fmt or "%X" in fmt:
        expanded = ""
        i = 0
        n = len(fmt)
        while i < n:
            c = fmt[i]
            if c == "%" and i + 1 < n:
                d = fmt[i + 1]
                if d == "c":
                    expanded = expanded + "%a %b %d %H:%M:%S %Y"
                elif d == "x":
                    expanded = expanded + "%m/%d/%y"
                elif d == "X":
                    expanded = expanded + "%H:%M:%S"
                else:
                    expanded = expanded + fmt[i:i + 2]
                i = i + 2
            else:
                expanded = expanded + fmt[i:i + 1]
                i = i + 1
        scan = expanded
    else:
        scan = fmt
    st = _ParseState()
    i = 0
    p = 0
    n = len(scan)
    m = len(data)
    while i < n:
        ch = scan[i]
        if ch == "%":
            if i + 1 >= n:
                raise ValueError(f"stray % in format '{fmt}'")
            p = _apply_directive(scan[i + 1], data, p, st, fmt)
            i = i + 2
        elif _is_space_char(ch):
            # A whitespace run in the format needs 1+ whitespace in the data.
            while i < n and _is_space_char(scan[i]):
                i = i + 1
            q = p
            while p < m and _is_space_char(data[p]):
                p = p + 1
            if p == q:
                _parse_fail(data, fmt)
        else:
            if p >= m or not _char_ieq(data[p], ch):
                _parse_fail(data, fmt)
            i = i + 1
            p = p + 1
    if p != m:
        raise ValueError("unconverted data remains: " + data[p:])

    # Post-processing (port of _strptime.py's resolution block).
    if st.has_iso_year:
        if st.has_julian:
            raise ValueError(
                "Day of the year directive '%j' is not compatible with "
                "ISO year directive '%G'. Use '%Y' instead.")
        if not st.has_iso_week or st.weekday < 0:
            raise ValueError(
                "ISO year directive '%G' must be used with the ISO week "
                "directive '%V' and a weekday directive "
                "('%A', '%a', '%w', or '%u').")
    elif st.has_iso_week:
        if not st.has_year or st.weekday < 0:
            raise ValueError(
                "ISO week directive '%V' must be used with the ISO year "
                "directive '%G' and a weekday directive "
                "('%A', '%a', '%w', or '%u').")
        raise ValueError(
            "ISO week directive '%V' is incompatible with the year "
            "directive '%Y'. Use the ISO year '%G' instead.")

    year = st.year
    leap_year_fix = False
    if not st.has_year:
        if st.month == 2 and st.day == 29:
            year = 1904  # first leap year of the 20th century
            leap_year_fix = True
        else:
            year = 1900

    month = st.month
    day = st.day
    julian = st.julian
    has_julian = st.has_julian
    if not has_julian and st.weekday >= 0:
        if st.week_of_year >= 0:
            julian = _calc_julian_from_week(year, st.week_of_year,
                                            st.weekday, st.week_starts_mon)
            has_julian = True
        elif st.has_iso_year and st.has_iso_week:
            # date.fromisocalendar(iso_year, iso_week, weekday + 1) --
            # incl. the week-53-only-in-53-week-years validation.
            year, month, day = _isoweek_to_gregorian(
                st.iso_year, st.iso_week, st.weekday + 1)
        if has_julian and julian <= 0:
            year = year - 1
            julian = julian + (366 if _is_leap(year) else 365)

    if has_julian:
        o = julian - 1 + _ymd2ord(year, 1, 1)
        if o < 1 or o > _MAXORDINAL:
            raise ValueError("day of year out of range")
        year, month, day = _ord2ymd(o)

    hour = st.hour
    if st.has_hour12:
        hour = st.hour12
        if st.ampm <= 0:
            # No indicator counts as AM; 12 AM is hour 0.
            if hour == 12:
                hour = 0
        else:
            if hour != 12:
                hour = hour + 12

    if leap_year_fix:
        # The caller asked for Feb 29 without a year; 1904 was only for
        # the week math. 1900-02-29 then fails validation like CPython.
        year = 1900

    off_us = 0
    if st.has_gmtoff:
        off_us = st.gmtoff * 1000000 + st.gmtoff_fraction
    # Construction happens in the datetime module (avoids a circular
    # import); zname "" means an unnamed offset (%Z only matches UTC/GMT,
    # so a named-empty tz cannot arise here).
    zname = st.zname if (st.has_zname and st.has_gmtoff) else ""
    return (year, month, day, hour, st.minute, st.second, st.fraction,
            st.has_gmtoff, off_us, zname)



def _int_digits(s: str) -> int:
    # Digit-strict int: CPython's C fromisoformat rejects '+12'/' 1' where
    # bare int() would accept them (the C parser is the parity oracle).
    if len(s) == 0:
        raise ValueError("empty numeric field")
    i = 0
    n = len(s)
    while i < n:
        if not _is_digit_char(s[i]):
            raise ValueError("non-digit in numeric field")
        i = i + 1
    return int(s)


def _find_iso_datetime_separator(dtstr: str) -> int:
    # Port of _pydatetime._find_isoformat_datetime_separator: where the
    # date part ends (the char there, ANY char, is the separator).
    n = len(dtstr)
    if n == 7:
        return 7
    if dtstr[4] == "-":
        if dtstr[5] == "W":
            if n < 8:
                raise ValueError("Invalid ISO string")
            if n > 8 and dtstr[8] == "-":
                if n == 9:
                    raise ValueError("Invalid ISO string")
                if n > 10 and _is_digit_char(dtstr[10]):
                    # YYYY-Www-## is ambiguous; assume the hyphen at 8 is
                    # the separator (CPython's best-effort rule).
                    return 8
                return 10
            return 8
        return 10
    if dtstr[4] == "W":
        # YYYYWww (7) or YYYYWwwd (8)
        idx = 7
        while idx < n:
            if not _is_digit_char(dtstr[idx]):
                break
            idx = idx + 1
        if idx < 9:
            return idx
        if idx % 2 == 0:
            return 7
        return 8
    return 8


def _parse_iso_date(dtstr: str) -> tuple[int, int, int]:
    # Port of _pydatetime._parse_isoformat_date; callers guarantee length
    # 7, 8 or 10.
    year = _int_digits(dtstr[0:4])
    has_sep = dtstr[4] == "-"
    pos = 5 if has_sep else 4
    if dtstr[pos:pos + 1] == "W":
        pos = pos + 1
        weekno = _int_digits(dtstr[pos:pos + 2])
        pos = pos + 2
        dayno = 1
        if len(dtstr) > pos:
            if (dtstr[pos:pos + 1] == "-") != has_sep:
                raise ValueError("Inconsistent use of dash separator")
            if has_sep:
                pos = pos + 1
            dayno = _int_digits(dtstr[pos:pos + 1])
        return _isoweek_to_gregorian(year, weekno, dayno)
    month = _int_digits(dtstr[pos:pos + 2])
    pos = pos + 2
    if (dtstr[pos:pos + 1] == "-") != has_sep:
        raise ValueError("Inconsistent use of dash separator")
    if has_sep:
        pos = pos + 1
    day = _int_digits(dtstr[pos:pos + 2])
    return (year, month, day)


def _parse_iso_time_comps(tstr: str) -> tuple[int, int, int, int]:
    # Port of _pydatetime._parse_hh_mm_ss_ff: HH[:?MM[:?SS[{.,}f{1,6}]]].
    # 7+ fraction digits TRUNCATE (unlike strptime %f, which errors) --
    # the two rules must not share a helper.
    n = len(tstr)
    hh = 0
    mm = 0
    ss = 0
    us = 0
    pos = 0
    has_sep = False
    comp = 0
    while comp < 3:
        if n - pos < 2:
            raise ValueError("Incomplete time component")
        v = _int_digits(tstr[pos:pos + 2])
        if comp == 0:
            hh = v
        elif comp == 1:
            mm = v
        else:
            ss = v
        pos = pos + 2
        nc = tstr[pos:pos + 1]
        if comp == 0:
            has_sep = nc == ":"
        if nc == "" or comp >= 2:
            break
        if has_sep and nc != ":":
            raise ValueError("Invalid time separator")
        if has_sep:
            pos = pos + 1
        comp = comp + 1
    if pos < n:
        fc = tstr[pos:pos + 1]
        if fc != "." and fc != ",":
            raise ValueError("Invalid microsecond component")
        pos = pos + 1
        remainder = n - pos
        to_parse = 6 if remainder >= 6 else remainder
        us = _int_digits(tstr[pos:pos + to_parse])
        if to_parse < 6:
            us = us * (10 ** (6 - to_parse))
        if remainder > to_parse:
            k = pos + to_parse
            while k < n:
                if not _is_digit_char(tstr[k]):
                    raise ValueError("Non-digit values in unparsed fraction")
                k = k + 1
    return (hh, mm, ss, us)


def _parse_iso_time(tstr: str) -> tuple[int, int, int, int, int, bool]:
    # Port of _pydatetime._parse_isoformat_time. Returns the time comps
    # plus (tz_offset_us, has_tz); 'Z' and an all-zero offset both mean
    # UTC (offset 0, aware).
    n = len(tstr)
    if n < 2:
        raise ValueError("Isoformat time too short")
    # CPython probes '-', then '+', then 'Z' (find priority, not position).
    tz_pos = tstr.find("-") + 1
    if tz_pos == 0:
        tz_pos = tstr.find("+") + 1
    if tz_pos == 0:
        tz_pos = tstr.find("Z") + 1
    timestr = tstr
    if tz_pos > 0:
        timestr = tstr[:tz_pos - 1]
    hh, mm, ss, us = _parse_iso_time_comps(timestr)
    if tz_pos == n and tstr[n - 1] == "Z":
        return (hh, mm, ss, us, 0, True)
    if tz_pos > 0:
        tzstr = tstr[tz_pos:]
        tn = len(tzstr)
        if tn == 0 or tn == 1 or tn == 3:
            raise ValueError("Malformed time zone string")
        tzh, tzm, tzs, tzus = _parse_iso_time_comps(tzstr)
        off = ((tzh * 3600 + tzm * 60 + tzs) * 1000000) + tzus
        if tstr[tz_pos - 1] == "-":
            off = -off
        return (hh, mm, ss, us, off, True)
    return (hh, mm, ss, us, 0, False)

