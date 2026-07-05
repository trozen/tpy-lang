# tpy: cpp_namespace("tpystd::datetime")
# The datetime module: date / time / datetime / timedelta / timezone.
# See docs/DATETIME_DESIGN.md. Value-typed frozen dataclasses; calendar math
# and formatting are pure-TPy for byte-parity with CPython (the cpy test phase
# resolves the real CPython datetime, so this file is compiled only by TPy).
# The only OS dependencies are epoch-now (time.time_ns) and the local-UTC-
# offset lookup (_bindings.hinnant_date over the tpy/stdlib/datetime.hpp
# facade).
#
# v1: timedelta + date. Integer constructor args only (CPython also accepts
# float args + float * / /; deferred, see the roadmap).
# v2: time + datetime (naive-only), now/utcnow/today/fromtimestamp/
# utcfromtimestamp/combine, date.today.
# v3: fixed-offset timezone + aware datetime (awareness is a runtime property;
# naive/aware mixing raises TypeError like CPython), strftime/strptime/
# fromisoformat, isoformat(timespec=), replace(), timestamp()/astimezone().
from __future__ import annotations
import time as _time
from typing import Final, overload
from dataclasses import dataclass
from tpy import Int8, Int16, Int32, Int64, String, ValueType
from _bindings import hinnant_date, tz_intern
from _datetime_cal import (
    _MAXORDINAL, _days_in_month, _is_leap, _ymd2ord, _ord2ymd,
)
from _datetime_fmt import _format_time, _offset_str, _strftime, _tz_label
from _datetime_parse import (
    _find_iso_datetime_separator, _parse_iso_date, _parse_iso_time,
    _strptime_impl,
)

_MAXORDINAL_DAYS = 999999999


@dataclass(frozen=True, order=True)
class timedelta(ValueType):
    # Normalized: 0 <= seconds < 86400, 0 <= microseconds < 10**6,
    # -999999999 <= days <= 999999999. Fields carry CPython's attribute names.
    days: Int32
    seconds: Int32
    microseconds: Int32

    def __init__(self, days: int = 0, seconds: int = 0, microseconds: int = 0,
                 milliseconds: int = 0, minutes: int = 0, hours: int = 0,
                 weeks: int = 0) -> None:
        d = days + weeks * 7
        s = seconds + minutes * 60 + hours * 3600
        us = microseconds + milliseconds * 1000
        # Normalize with Python floor divmod so seconds/microseconds stay
        # non-negative and the overflow carries into days.
        carry_s, us = divmod(us, 1000000)
        s = s + carry_s
        carry_d, s = divmod(s, 86400)
        d = d + carry_d
        if d < -_MAXORDINAL_DAYS or d > _MAXORDINAL_DAYS:
            raise OverflowError("timedelta # of days is too large")
        self.days = d
        self.seconds = s
        self.microseconds = us

    def _to_microseconds(self) -> int:
        # Widen the Int32 fields to BigInt before combining: the max total
        # (~8.6e19 us) exceeds Int64, and an Int32 intermediate overflows even
        # for an hour (3600 * 10**6 > Int32 max).
        return (int(self.days) * 86400 + int(self.seconds)) * 1000000 + int(self.microseconds)

    def total_seconds(self) -> float:
        return self._to_microseconds() / 1000000

    def __add__(self, other: timedelta) -> timedelta:
        return timedelta(days=self.days + other.days,
                         seconds=self.seconds + other.seconds,
                         microseconds=self.microseconds + other.microseconds)

    def __sub__(self, other: timedelta) -> timedelta:
        return timedelta(days=self.days - other.days,
                         seconds=self.seconds - other.seconds,
                         microseconds=self.microseconds - other.microseconds)

    def __neg__(self) -> timedelta:
        return timedelta(days=-self.days, seconds=-self.seconds,
                         microseconds=-self.microseconds)

    def __pos__(self) -> timedelta:
        return timedelta(days=self.days, seconds=self.seconds,
                         microseconds=self.microseconds)

    def __abs__(self) -> timedelta:
        if self.days < 0:
            return -self
        return timedelta(days=self.days, seconds=self.seconds,
                         microseconds=self.microseconds)

    def __mul__(self, other: int) -> timedelta:
        return timedelta(days=self.days * other, seconds=self.seconds * other,
                         microseconds=self.microseconds * other)

    def __rmul__(self, other: int) -> timedelta:
        return timedelta(days=self.days * other, seconds=self.seconds * other,
                         microseconds=self.microseconds * other)

    @overload
    def __floordiv__(self, other: timedelta) -> int:
        return self._to_microseconds() // other._to_microseconds()

    @overload
    def __floordiv__(self, other: int) -> timedelta:
        return timedelta(microseconds=self._to_microseconds() // other)

    # v1: true division only by another timedelta (the ratio). `td / number`
    # (round-half-to-even -> timedelta) is deferred with the float surface.
    def __truediv__(self, other: timedelta) -> float:
        return self._to_microseconds() / other._to_microseconds()

    def __mod__(self, other: timedelta) -> timedelta:
        return timedelta(microseconds=self._to_microseconds() % other._to_microseconds())

    def __bool__(self) -> bool:
        return self.days != 0 or self.seconds != 0 or self.microseconds != 0

    def __repr__(self) -> str:
        args = ""
        if self.days != 0:
            args = f"days={self.days}"
        if self.seconds != 0:
            if args != "":
                args = args + ", "
            args = args + f"seconds={self.seconds}"
        if self.microseconds != 0:
            if args != "":
                args = args + ", "
            args = args + f"microseconds={self.microseconds}"
        if args == "":
            args = "0"
        return f"datetime.timedelta({args})"

    def __str__(self) -> str:
        mm, ss = divmod(self.seconds, 60)
        hh, mm = divmod(mm, 60)
        s = f"{hh}:{mm:02d}:{ss:02d}"
        if self.days != 0:
            plural = "s" if (self.days != 1 and self.days != -1) else ""
            s = f"{self.days} day{plural}, " + s
        if self.microseconds != 0:
            s = s + f".{self.microseconds:06d}"
        return s


def _check_date_fields(year: int, month: int, day: int) -> None:
    if year < 1 or year > 9999:
        raise ValueError("year is out of range")
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if day < 1 or day > _days_in_month(year, month):
        raise ValueError("day is out of range for month")


def _check_time_fields(hour: int, minute: int, second: int, microsecond: int) -> None:
    if hour < 0 or hour > 23:
        raise ValueError("hour must be in 0..23")
    if minute < 0 or minute > 59:
        raise ValueError("minute must be in 0..59")
    if second < 0 or second > 59:
        raise ValueError("second must be in 0..59")
    if microsecond < 0 or microsecond > 999999:
        raise ValueError("microsecond must be in 0..999999")


@dataclass(frozen=True)
class timezone(ValueType):
    # Fixed-offset tz -- the only kind in the closed value-typed set
    # (chrono-inspired; a ZoneInfo widening is deferred, user tzinfo
    # subclasses are permanently unsupported). Packed to 16B, trivially
    # copyable: the offset lives inline (hot: eq/hash/arithmetic), the
    # name in the process-global intern table (cold: tzname/repr; id 0 =
    # unnamed, so timezone(off) and timezone(off, "") stay distinct --
    # CPython stores None vs ""). Field layout mirrors datetime's inline
    # tz block so ingestion/reconstruction are raw field copies.
    _off_us: Int64
    _name_id: Int32

    def __init__(self, offset: timedelta, name: str | None = None) -> None:
        us = offset._to_microseconds()
        if us <= -86400000000 or us >= 86400000000:
            raise ValueError("offset must be a timedelta strictly between "
                             "-timedelta(hours=24) and timedelta(hours=24).")
        # The range check bounds us under 2**37, so the Int64 store is safe.
        self._off_us = us
        self._name_id = 0 if name is None else tz_intern.intern_name(name)

    def utcoffset(self, dt: "datetime | None") -> timedelta:
        return timedelta(microseconds=int(self._off_us))

    def tzname(self, dt: "datetime | None") -> String:
        if self._name_id != 0:
            return tz_intern.name_at(self._name_id)
        return _tz_label(int(self._off_us))

    def dst(self, dt: "datetime | None") -> timedelta | None:
        return None

    def fromutc(self, dt: "datetime") -> "datetime":
        tz = dt.tzinfo
        if tz is None or tz != self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        return dt + self.utcoffset(dt)

    # CPython compares timezones by offset only; the name is cosmetic.
    def __eq__(self, other: "timezone") -> bool:
        return self._off_us == other._off_us

    def __hash__(self) -> int:
        return hash(self._off_us)

    def __repr__(self) -> str:
        if self._name_id == 0 and self._off_us == 0:
            # Value-equal to CPython's interned timezone.utc singleton.
            return "datetime.timezone.utc"
        off_repr = repr(timedelta(microseconds=int(self._off_us)))
        if self._name_id != 0:
            name = tz_intern.name_at(self._name_id)
            return f"datetime.timezone({off_repr}, {repr(name)})"
        return f"datetime.timezone({off_repr})"

    def __str__(self) -> str:
        return self.tzname(None)


# CPython 3.11+ module-level alias for timezone.utc (the class attribute
# is not expressible: a class-level constant of the record's own type is
# outside Final's constexpr model, so the attribute access is a loud
# compile error and UTC is the supported spelling).
UTC: timezone = timezone(timedelta())


@dataclass(frozen=True, order=True)
class date(ValueType):
    # Packed to 4B: narrow private storage (validated ranges fit exactly),
    # public attrs are Int32-widening properties so user arithmetic never
    # touches the narrow types. Storage stays in significance order --
    # order=True tuple comparison over it is the chronological order.
    _y: Int16
    _mo: Int8
    _d: Int8

    def __init__(self, year: int, month: int, day: int) -> None:
        _check_date_fields(year, month, day)
        self._y = year
        self._mo = month
        self._d = day

    @property
    def year(self) -> Int32:
        return Int32(self._y)

    @property
    def month(self) -> Int32:
        return Int32(self._mo)

    @property
    def day(self) -> Int32:
        return Int32(self._d)

    @staticmethod
    def today() -> "date":
        dt = datetime.now()
        return date(int(dt.year), int(dt.month), int(dt.day))

    @staticmethod
    def fromordinal(n: int) -> "date":
        y, m, d = _ord2ymd(n)
        return date(y, m, d)

    @staticmethod
    def fromisoformat(date_string: str) -> "date":
        n = len(date_string)
        if n != 7 and n != 8 and n != 10:
            raise ValueError(f"Invalid isoformat string: '{date_string}'")
        try:
            y, mo, d = _parse_iso_date(date_string)
            return date(y, mo, d)
        except ValueError:
            raise ValueError(f"Invalid isoformat string: '{date_string}'")

    def toordinal(self) -> int:
        return _ymd2ord(int(self.year), int(self.month), int(self.day))

    def weekday(self) -> int:
        return (self.toordinal() + 6) % 7

    def isoweekday(self) -> int:
        return self.weekday() + 1

    def replace(self, year: int | None = None, month: int | None = None,
                day: int | None = None) -> "date":
        y = year if year is not None else int(self.year)
        mo = month if month is not None else int(self.month)
        d = day if day is not None else int(self.day)
        return date(y, mo, d)

    def strftime(self, format: str) -> str:
        return _strftime(format, self.year, self.month, self.day,
                         0, 0, 0, 0, False, 0, "")

    def isoformat(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def __str__(self) -> str:
        return self.isoformat()

    def __repr__(self) -> str:
        return f"datetime.date({self.year}, {self.month}, {self.day})"

    def __add__(self, other: timedelta) -> "date":
        o = self.toordinal() + other.days
        if o < 1 or o > _MAXORDINAL:
            raise OverflowError("result out of range")
        return date.fromordinal(o)

    @overload
    def __sub__(self, other: "date") -> timedelta:
        return timedelta(days=self.toordinal() - other.toordinal())

    @overload
    def __sub__(self, other: timedelta) -> "date":
        o = self.toordinal() - other.days
        if o < 1 or o > _MAXORDINAL:
            raise OverflowError("result out of range")
        return date.fromordinal(o)


@dataclass(frozen=True, order=True)
class time(ValueType):
    # Naive only (no tzinfo/fold; aware time is out of scope -- see the
    # roadmap). Packed to 8B: narrow private storage in significance order
    # (order=True tuple comparison stays chronological), public attrs are
    # Int32-widening properties.
    _hh: Int8
    _mm: Int8
    _ss: Int8
    _us: Int32

    def __init__(self, hour: int = 0, minute: int = 0, second: int = 0,
                 microsecond: int = 0) -> None:
        _check_time_fields(hour, minute, second, microsecond)
        self._hh = hour
        self._mm = minute
        self._ss = second
        self._us = microsecond

    @property
    def hour(self) -> Int32:
        return Int32(self._hh)

    @property
    def minute(self) -> Int32:
        return Int32(self._mm)

    @property
    def second(self) -> Int32:
        return Int32(self._ss)

    @property
    def microsecond(self) -> Int32:
        return self._us

    @staticmethod
    def fromisoformat(time_string: str) -> "time":
        s = time_string
        if s.startswith("T"):
            s = s[1:]
        try:
            hh, mm, ss, us, off, has_tz = _parse_iso_time(s)
        except ValueError:
            raise ValueError(f"Invalid isoformat string: '{time_string}'")
        if has_tz:
            # CPython returns an aware time here; TPy's time is naive-only
            # (aware time deferred) -- loud rejection, not silent dropping.
            raise ValueError(
                "aware time is not supported (offset suffix rejected; "
                "aware time is deferred in TPy)")
        try:
            return time(hh, mm, ss, us)
        except ValueError:
            raise ValueError(f"Invalid isoformat string: '{time_string}'")

    def replace(self, hour: int | None = None, minute: int | None = None,
                second: int | None = None,
                microsecond: int | None = None) -> "time":
        # No tzinfo param: aware time is out of scope (see the roadmap).
        hh = hour if hour is not None else int(self.hour)
        mm = minute if minute is not None else int(self.minute)
        ss = second if second is not None else int(self.second)
        us = microsecond if microsecond is not None else int(self.microsecond)
        return time(hh, mm, ss, us)

    def strftime(self, format: str) -> str:
        # CPython formats a time through the 1900-01-01 timetuple.
        return _strftime(format, 1900, 1, 1, self.hour, self.minute,
                         self.second, self.microsecond, False, 0, "")

    def isoformat(self, timespec: str = "auto") -> str:
        return _format_time(self.hour, self.minute, self.second,
                            self.microsecond, timespec)

    def __str__(self) -> str:
        return self.isoformat()

    def __repr__(self) -> str:
        # CPython trims trailing zero components but always keeps
        # hour and minute.
        s = f"datetime.time({self.hour}, {self.minute}"
        if self.second != 0 or self.microsecond != 0:
            s = s + f", {self.second}"
            if self.microsecond != 0:
                s = s + f", {self.microsecond}"
        return s + ")"


_EPOCH_ORDINAL: int = 719163  # date(1970, 1, 1).toordinal()
_TIME_T_MAX: int = 9223372036854775807  # 64-bit time_t, matching CPython's bound


def _local_epoch_s(u: int) -> int:
    # local(u) in CPython's _mktime: the wall-clock epoch second the local
    # zone shows at UTC epoch second u. Callers pass values derived from
    # in-range datetimes (|u| < ~3e11), so the Int64 narrowing cannot panic.
    return u + int(hinnant_date.local_utc_offset_seconds(Int64(u)))


def _timestamp_to_us(t: float) -> int:
    # CPython's _fromtimestamp split: whole seconds via truncation, then
    # round-half-even of the fractional part in microseconds. Keeping
    # round() on the small fractional value (not on t * 10**6) matches
    # CPython and stays exact in double.
    whole = int(t)
    frac = t - float(whole)
    us: int = round(frac * 1000000)
    return whole * 1000000 + us


@dataclass(frozen=True)
class datetime(ValueType):
    # Composed, not a date subclass: cross-type date/datetime comparison is
    # a compile error (documented divergence). No order=True: awareness makes
    # tuple comparison wrong, so ordering operators are hand-written.
    # Awareness is a RUNTIME property; mixing naive and aware in ordering/
    # subtraction raises TypeError exactly like CPython. No fold (deferred).
    # Packed to 24B, trivially copyable: narrow private storage behind
    # Int32-widening properties, and the tz inline as offset + interned
    # name id (the timezone value is reconstructed only when .tzinfo is
    # read -- cold; the offset drives all hot paths directly). Field order
    # is packing order (widest first); nothing here relies on declaration
    # order (all comparisons/hash/repr are hand-written).
    _tz_off_us: Int64
    _us: Int32
    _tz_name_id: Int32
    _y: Int16
    _mo: Int8
    _d: Int8
    _hh: Int8
    _mm: Int8
    _ss: Int8
    _aware: bool

    def __init__(self, year: int, month: int, day: int, hour: int = 0,
                 minute: int = 0, second: int = 0, microsecond: int = 0,
                 tzinfo: timezone | None = None) -> None:
        _check_date_fields(year, month, day)
        _check_time_fields(hour, minute, second, microsecond)
        self._y = year
        self._mo = month
        self._d = day
        self._hh = hour
        self._mm = minute
        self._ss = second
        self._us = microsecond
        # Locals first, stores unconditional: a branch-assigned field
        # draws the not-initialized-before-ctor-body warning.
        tz_off = 0
        tz_id = 0
        if tzinfo is not None:
            tz_off = int(tzinfo._off_us)
            tz_id = int(tzinfo._name_id)
        self._aware = tzinfo is not None
        self._tz_off_us = tz_off
        self._tz_name_id = tz_id

    @property
    def year(self) -> Int32:
        return Int32(self._y)

    @property
    def month(self) -> Int32:
        return Int32(self._mo)

    @property
    def day(self) -> Int32:
        return Int32(self._d)

    @property
    def hour(self) -> Int32:
        return Int32(self._hh)

    @property
    def minute(self) -> Int32:
        return Int32(self._mm)

    @property
    def second(self) -> Int32:
        return Int32(self._ss)

    @property
    def microsecond(self) -> Int32:
        return self._us

    @property
    def tzinfo(self) -> timezone | None:
        if not self._aware:
            return None
        off = timedelta(microseconds=int(self._tz_off_us))
        if self._tz_name_id == 0:
            return timezone(off)
        return timezone(off, tz_intern.name_at(self._tz_name_id))

    def _epoch_us(self) -> int:
        # Wall-clock microseconds since the epoch, ignoring the tz.
        secs = (int(self._hh) * 3600 + int(self._mm) * 60
                + int(self._ss))
        return (((self.toordinal() - _EPOCH_ORDINAL) * 86400 + secs)
                * 1000000 + int(self._us))

    def _utc_us(self) -> int:
        # UTC microseconds since the epoch; equals wall clock when naive, so
        # it doubles as the eq/hash key for both awareness states (naive and
        # aware values never compare equal -- __eq__ dispatches first).
        off = 0
        if self._aware:
            off = int(self._tz_off_us)
        return self._epoch_us() - off

    def utcoffset(self) -> timedelta | None:
        if not self._aware:
            return None
        return timedelta(microseconds=int(self._tz_off_us))

    def tzname(self) -> str | None:
        if not self._aware:
            return None
        if self._tz_name_id != 0:
            return tz_intern.name_at(self._tz_name_id)
        return _tz_label(int(self._tz_off_us))

    def dst(self) -> timedelta | None:
        return None

    def replace(self, year: int | None = None, month: int | None = None,
                day: int | None = None, hour: int | None = None,
                minute: int | None = None, second: int | None = None,
                microsecond: int | None = None,
                tzinfo: timezone | bool | None = True) -> "datetime":
        # CPython's own sentinel: tzinfo defaults to True (keep current);
        # passing a timezone sets it, passing None drops it. A record value
        # cannot be a TPy param default, so the bool arm IS the omitted case.
        y = year if year is not None else int(self.year)
        mo = month if month is not None else int(self.month)
        d = day if day is not None else int(self.day)
        hh = hour if hour is not None else int(self.hour)
        mm = minute if minute is not None else int(self.minute)
        ss = second if second is not None else int(self.second)
        us = microsecond if microsecond is not None else int(self.microsecond)
        tz: timezone | None = None
        if isinstance(tzinfo, bool):
            if not tzinfo:
                raise TypeError("tzinfo argument must be None or a timezone")
            tz = self.tzinfo
        elif isinstance(tzinfo, timezone):
            tz = tzinfo
        return datetime(y, mo, d, hh, mm, ss, us, tz)

    def _mktime_s(self, fold: int) -> int:
        # CPython's _mktime: iteratively solve t = local(u) for the UTC
        # epoch second u whose local reading is this wall clock. A single
        # forward offset lookup is silently wrong in the 1-2h window around
        # every DST transition; the fixed-point probe handles gaps (fold=0
        # resolves to the later instant) and folds (earlier instant).
        t = self._epoch_us() // 1000000
        a = _local_epoch_s(t) - t
        u1 = t - a
        t1 = _local_epoch_s(u1)
        b = a
        if t1 == t:
            # One solution found; probe a day away to detect a fold.
            u2 = u1 + (-86400 if fold == 0 else 86400)
            b = _local_epoch_s(u2) - u2
            if a == b:
                return u1
        else:
            b = t1 - u1
        u2 = t - b
        t2 = _local_epoch_s(u2)
        if t2 == t:
            return u2
        if t1 == t:
            return u1
        # Neither candidate reproduces t: the wall time is in a DST gap.
        if fold == 0:
            return max(u1, u2)
        return min(u1, u2)

    def timestamp(self) -> float:
        if not self._aware:
            # Naive means system-local wall clock (CPython semantics).
            return self._mktime_s(0) + int(self._us) / 1000000
        return self._utc_us() / 1000000

    def _local_timezone(self) -> timezone:
        # The system zone at this value's instant, as a fixed offset +
        # libc-style abbreviation ("CET") -- what astimezone(None) attaches.
        if not self._aware:
            ts = self._mktime_s(0)
            ts2 = self._mktime_s(1)
            # In a gap or fold the two fold solves disagree; fold=0 picks
            # the earlier offset's reading (CPython's rule).
            if ts2 != ts and ts2 < ts:
                ts = ts2
        else:
            ts = self._utc_us() // 1000000
        off = int(hinnant_date.local_utc_offset_seconds(Int64(ts)))
        name = hinnant_date.local_zone_abbrev(Int64(ts))
        return timezone(timedelta(seconds=off), name)

    def astimezone(self, tz: timezone | None = None) -> "datetime":
        # A naive value is interpreted as system-local wall-clock time
        # (CPython: astimezone does NOT raise on naive input -- unlike
        # the mixing rules for comparison/subtraction).
        if tz is None:
            tz = self._local_timezone()
        if self._aware:
            my_off_us = int(self._tz_off_us)
        else:
            my_off_us = int(self._local_timezone()._off_us)
        delta = int(tz._off_us) - my_off_us
        shifted = self + timedelta(microseconds=delta)
        return shifted.replace(tzinfo=tz)

    @staticmethod
    def strptime(date_string: str, format: str) -> "datetime":
        y, mo, d, hh, mm, ss, us, has_tz, off_us, zname = _strptime_impl(
            date_string, format)
        tz: timezone | None = None
        if has_tz:
            delta = timedelta(microseconds=off_us)
            if zname != "":
                tz = timezone(delta, zname)
            else:
                tz = timezone(delta)
        return datetime(y, mo, d, hh, mm, ss, us, tz)

    @staticmethod
    def fromisoformat(date_string: str) -> "datetime":
        if len(date_string) < 7:
            raise ValueError(f"Invalid isoformat string: '{date_string}'")
        try:
            sep_loc = _find_iso_datetime_separator(date_string)
            y, mo, d = _parse_iso_date(date_string[0:sep_loc])
        except ValueError:
            raise ValueError(f"Invalid isoformat string: '{date_string}'")
        tstr = date_string[sep_loc + 1:]
        # BigInt annotations: the unpack below yields BigInt; a bare 0
        # would infer Int32 and fail the assignment.
        hh: int = 0
        mm: int = 0
        ss: int = 0
        us: int = 0
        tz: timezone | None = None
        if len(tstr) > 0:
            try:
                hh, mm, ss, us, off, has_tz = _parse_iso_time(tstr)
                if has_tz:
                    tz = timezone(timedelta(microseconds=off))
            except ValueError:
                raise ValueError(
                    f"Invalid isoformat string: '{date_string}'")
        # The ctor's own range errors surface unwrapped (CPython does not
        # wrap them for datetime, unlike date.fromisoformat).
        return datetime(y, mo, d, hh, mm, ss, us, tz)

    @staticmethod
    def combine(d: date, t: time,
                tzinfo: timezone | None = None) -> "datetime":
        return datetime(int(d.year), int(d.month), int(d.day), int(t.hour),
                        int(t.minute), int(t.second), int(t.microsecond),
                        tzinfo)

    @staticmethod
    def _from_epoch_us(us: int, use_local: bool,
                       tz: timezone | None = None) -> "datetime":
        # Civil datetime from epoch microseconds, shifted by the fixed tz
        # offset (aware result) or the OS tz database's local UTC offset for
        # that instant (naive local result). Floor divmod keeps pre-1970
        # (negative) timestamps correct.
        epoch_s = us // 1000000
        # Beyond time_t: CPython raises OverflowError. The check must run
        # before the Int64 narrowing at the native call below, which would
        # otherwise panic uncatchably. (glibc additionally fails with
        # OSError in a narrower band; not emulated -- see DATETIME_DESIGN.)
        if epoch_s < -_TIME_T_MAX - 1 or epoch_s > _TIME_T_MAX:
            raise OverflowError("timestamp out of range")
        days, rem = divmod(us, 86400000000)
        o = days + _EPOCH_ORDINAL
        # CPython rejects a UTC instant outside year 1..9999 even when the
        # offset would shift it back into range (the pre-year-1 LMT band),
        # so check before AND after applying the offset. ValueError matches
        # CPython's type ("year N is out of range").
        if o < 1 or o > _MAXORDINAL:
            raise ValueError("year is out of range")
        off = 0
        if tz is not None:
            off = int(tz._off_us)
        elif use_local:
            off = int(hinnant_date.local_utc_offset_seconds(
                Int64(epoch_s))) * 1000000
        if off != 0:
            us = us + off
            days, rem = divmod(us, 86400000000)
            o = days + _EPOCH_ORDINAL
            if o < 1 or o > _MAXORDINAL:
                raise ValueError("year is out of range")
        y, mo, d = _ord2ymd(o)
        s, us_part = divmod(rem, 1000000)
        hh, rem_s = divmod(s, 3600)
        mm, ss = divmod(rem_s, 60)
        return datetime(y, mo, d, hh, mm, ss, us_part, tz)

    @staticmethod
    def fromtimestamp(t: float, tz: timezone | None = None) -> "datetime":
        return datetime._from_epoch_us(_timestamp_to_us(t), tz is None, tz)

    @staticmethod
    def utcfromtimestamp(t: float) -> "datetime":
        return datetime._from_epoch_us(_timestamp_to_us(t), False)

    @staticmethod
    def now(tz: timezone | None = None) -> "datetime":
        return datetime._from_epoch_us(int(_time.time_ns()) // 1000,
                                       tz is None, tz)

    @staticmethod
    def utcnow() -> "datetime":
        return datetime._from_epoch_us(int(_time.time_ns()) // 1000, False)

    @staticmethod
    def today() -> "datetime":
        return datetime.now()

    def toordinal(self) -> int:
        return _ymd2ord(int(self.year), int(self.month), int(self.day))

    def weekday(self) -> int:
        return (self.toordinal() + 6) % 7

    def isoweekday(self) -> int:
        return self.weekday() + 1

    def strftime(self, format: str) -> str:
        if not self._aware:
            return _strftime(format, self.year, self.month, self.day,
                             self.hour, self.minute, self.second,
                             self.microsecond, False, 0, "")
        zone = self.tzname()
        return _strftime(format, self.year, self.month, self.day,
                         self.hour, self.minute, self.second,
                         self.microsecond, True,
                         int(self._tz_off_us), zone if zone is not None else "")

    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        s = (f"{self.year:04d}-{self.month:02d}-{self.day:02d}{sep}"
             + _format_time(self.hour, self.minute, self.second,
                            self.microsecond, timespec))
        if self._aware:
            s = s + _offset_str(int(self._tz_off_us), ":")
        return s

    def __str__(self) -> str:
        return self.isoformat(" ")

    def __repr__(self) -> str:
        # CPython trims trailing zero seconds/microseconds but always keeps
        # year..minute; an aware value appends the tzinfo keyword arg.
        s = (f"datetime.datetime({self.year}, {self.month}, {self.day}, "
             f"{self.hour}, {self.minute}")
        if self.second != 0 or self.microsecond != 0:
            s = s + f", {self.second}"
            if self.microsecond != 0:
                s = s + f", {self.microsecond}"
        tz = self.tzinfo
        if tz is not None:
            s = s + f", tzinfo={repr(tz)}"
        return s + ")"

    def _cmp(self, other: "datetime") -> int:
        # Ordering across awareness states is a runtime TypeError (CPython
        # parity); aware pairs compare by UTC instant.
        self_aware = self._aware
        other_aware = other._aware
        if self_aware != other_aware:
            raise TypeError(
                "can't compare offset-naive and offset-aware datetimes")
        if self_aware:
            a = self._utc_us()
            b = other._utc_us()
            if a != b:
                return -1 if a < b else 1
            return 0
        if self.year != other.year:
            return -1 if self.year < other.year else 1
        if self.month != other.month:
            return -1 if self.month < other.month else 1
        if self.day != other.day:
            return -1 if self.day < other.day else 1
        if self.hour != other.hour:
            return -1 if self.hour < other.hour else 1
        if self.minute != other.minute:
            return -1 if self.minute < other.minute else 1
        if self.second != other.second:
            return -1 if self.second < other.second else 1
        if self.microsecond != other.microsecond:
            return -1 if self.microsecond < other.microsecond else 1
        return 0

    def __eq__(self, other: "datetime") -> bool:
        # Unlike ordering, naive == aware is False, not an error.
        if self._aware != other._aware:
            return False
        return self._cmp(other) == 0

    def __hash__(self) -> int:
        return hash(self._utc_us())

    def __lt__(self, other: "datetime") -> bool:
        return self._cmp(other) < 0

    def __le__(self, other: "datetime") -> bool:
        return self._cmp(other) <= 0

    def __gt__(self, other: "datetime") -> bool:
        return self._cmp(other) > 0

    def __ge__(self, other: "datetime") -> bool:
        return self._cmp(other) >= 0

    def __add__(self, other: timedelta) -> "datetime":
        # Wall-clock arithmetic: an aware value keeps its tzinfo untouched.
        delta = timedelta(self.toordinal(), hours=int(self.hour),
                          minutes=int(self.minute), seconds=int(self.second),
                          microseconds=int(self.microsecond))
        delta = delta + other
        if delta.days < 1 or delta.days > _MAXORDINAL:
            raise OverflowError("result out of range")
        y, mo, d = _ord2ymd(int(delta.days))
        hh, rem = divmod(int(delta.seconds), 3600)
        mm, ss = divmod(rem, 60)
        return datetime(y, mo, d, hh, mm, ss, int(delta.microseconds),
                        self.tzinfo)

    @overload
    def __sub__(self, other: "datetime") -> timedelta:
        if self._aware != other._aware:
            raise TypeError(
                "can't subtract offset-naive and offset-aware datetimes")
        return timedelta(microseconds=self._utc_us() - other._utc_us())

    @overload
    def __sub__(self, other: timedelta) -> "datetime":
        return self + (-other)
