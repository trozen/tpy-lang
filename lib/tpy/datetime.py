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
from __future__ import annotations
import time as _time
from typing import overload
from dataclasses import dataclass
from tpy import Int32, Int64, ValueType
from _bindings import hinnant_date

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


# Calendar constants/helpers, ported from CPython's datetime.py. The lists are
# annotated list[int] (BigInt elements): the calendar math mixes them with
# divmod-derived BigInt locals, and the inferred list[Int32] would not compare.
_MAXORDINAL: int = 3652059  # date.max.toordinal(): 9999-12-31
_DAYS_IN_MONTH: list[int] = [-1, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_DAYS_BEFORE_MONTH: list[int] = [-1, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _days_before_year(year: int) -> int:
    y = year - 1
    return y * 365 + y // 4 - y // 100 + y // 400


def _days_before_month(year: int, month: int) -> int:
    extra = 1 if (month > 2 and _is_leap(year)) else 0
    return _DAYS_BEFORE_MONTH[month] + extra


def _ymd2ord(year: int, month: int, day: int) -> int:
    return _days_before_year(year) + _days_before_month(year, month) + day


def _ord2ymd(n: int) -> tuple[int, int, int]:
    n = n - 1
    n400, n = divmod(n, 146097)
    year = n400 * 400 + 1
    n100, n = divmod(n, 36524)
    n4, n = divmod(n, 1461)
    n1, n = divmod(n, 365)
    year = year + n100 * 100 + n4 * 4 + n1
    if n1 == 4 or n100 == 4:
        return (year - 1, 12, 31)
    leapyear = n1 == 3 and (n4 != 24 or n100 == 3)
    month = (n + 50) >> 5
    preceding = _DAYS_BEFORE_MONTH[month] + (1 if (month > 2 and leapyear) else 0)
    if preceding > n:
        month = month - 1
        preceding = preceding - (_DAYS_IN_MONTH[month] + (1 if (month == 2 and leapyear) else 0))
    n = n - preceding
    return (year, month, n + 1)


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


def _format_time(hh: Int32, mm: Int32, ss: Int32, us: Int32) -> str:
    # CPython's timespec='auto': seconds always shown, microseconds only
    # when non-zero.
    s = f"{hh:02d}:{mm:02d}:{ss:02d}"
    if us != 0:
        s = s + f".{us:06d}"
    return s


@dataclass(frozen=True, order=True)
class date(ValueType):
    year: Int32
    month: Int32
    day: Int32

    def __init__(self, year: int, month: int, day: int) -> None:
        _check_date_fields(year, month, day)
        self.year = year
        self.month = month
        self.day = day

    @staticmethod
    def today() -> "date":
        dt = datetime.now()
        return date(int(dt.year), int(dt.month), int(dt.day))

    @staticmethod
    def fromordinal(n: int) -> "date":
        y, m, d = _ord2ymd(n)
        return date(y, m, d)

    def toordinal(self) -> int:
        return _ymd2ord(int(self.year), int(self.month), int(self.day))

    def weekday(self) -> int:
        return (self.toordinal() + 6) % 7

    def isoweekday(self) -> int:
        return self.weekday() + 1

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
    # roadmap). Fields are declared in significance order, so order=True
    # tuple comparison is the correct chronological order.
    hour: Int32
    minute: Int32
    second: Int32
    microsecond: Int32

    def __init__(self, hour: int = 0, minute: int = 0, second: int = 0,
                 microsecond: int = 0) -> None:
        _check_time_fields(hour, minute, second, microsecond)
        self.hour = hour
        self.minute = minute
        self.second = second
        self.microsecond = microsecond

    def isoformat(self) -> str:
        return _format_time(self.hour, self.minute, self.second,
                            self.microsecond)

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
    # Naive only (no tzinfo/fold; fixed-offset awareness is v3 -- see the
    # roadmap). Composed, not a date subclass: cross-type date/datetime
    # comparison is a compile error (documented divergence). No order=True:
    # once tzinfo is a field, tuple comparison is wrong, so the ordering
    # operators are hand-written from the start.
    year: Int32
    month: Int32
    day: Int32
    hour: Int32
    minute: Int32
    second: Int32
    microsecond: Int32

    def __init__(self, year: int, month: int, day: int, hour: int = 0,
                 minute: int = 0, second: int = 0,
                 microsecond: int = 0) -> None:
        _check_date_fields(year, month, day)
        _check_time_fields(hour, minute, second, microsecond)
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self.minute = minute
        self.second = second
        self.microsecond = microsecond

    @staticmethod
    def combine(d: date, t: time) -> "datetime":
        return datetime(int(d.year), int(d.month), int(d.day), int(t.hour),
                        int(t.minute), int(t.second), int(t.microsecond))

    @staticmethod
    def _from_epoch_us(us: int, use_local: bool) -> "datetime":
        # Civil datetime from epoch microseconds. Local conversion adds the
        # OS tz database's UTC offset for that instant; floor divmod keeps
        # pre-1970 (negative) timestamps correct.
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
        # local offset would shift it back into range (the pre-year-1 LMT
        # band), so check before AND after applying the offset. ValueError
        # matches CPython's type ("year N is out of range").
        if o < 1 or o > _MAXORDINAL:
            raise ValueError("year is out of range")
        if use_local:
            offset = hinnant_date.local_utc_offset_seconds(Int64(epoch_s))
            us = us + int(offset) * 1000000
            days, rem = divmod(us, 86400000000)
            o = days + _EPOCH_ORDINAL
            if o < 1 or o > _MAXORDINAL:
                raise ValueError("year is out of range")
        y, mo, d = _ord2ymd(o)
        s, us_part = divmod(rem, 1000000)
        hh, rem_s = divmod(s, 3600)
        mm, ss = divmod(rem_s, 60)
        return datetime(y, mo, d, hh, mm, ss, us_part)

    @staticmethod
    def fromtimestamp(t: float) -> "datetime":
        return datetime._from_epoch_us(_timestamp_to_us(t), True)

    @staticmethod
    def utcfromtimestamp(t: float) -> "datetime":
        return datetime._from_epoch_us(_timestamp_to_us(t), False)

    @staticmethod
    def now() -> "datetime":
        return datetime._from_epoch_us(int(_time.time_ns()) // 1000, True)

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

    def isoformat(self, sep: str = "T") -> str:
        return (f"{self.year:04d}-{self.month:02d}-{self.day:02d}{sep}"
                + _format_time(self.hour, self.minute, self.second,
                               self.microsecond))

    def __str__(self) -> str:
        return self.isoformat(" ")

    def __repr__(self) -> str:
        # CPython trims trailing zero seconds/microseconds but always keeps
        # year..minute.
        s = (f"datetime.datetime({self.year}, {self.month}, {self.day}, "
             f"{self.hour}, {self.minute}")
        if self.second != 0 or self.microsecond != 0:
            s = s + f", {self.second}"
            if self.microsecond != 0:
                s = s + f", {self.microsecond}"
        return s + ")"

    def _cmp(self, other: "datetime") -> int:
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

    def __lt__(self, other: "datetime") -> bool:
        return self._cmp(other) < 0

    def __le__(self, other: "datetime") -> bool:
        return self._cmp(other) <= 0

    def __gt__(self, other: "datetime") -> bool:
        return self._cmp(other) > 0

    def __ge__(self, other: "datetime") -> bool:
        return self._cmp(other) >= 0

    def __add__(self, other: timedelta) -> "datetime":
        delta = timedelta(self.toordinal(), hours=int(self.hour),
                          minutes=int(self.minute), seconds=int(self.second),
                          microseconds=int(self.microsecond))
        delta = delta + other
        if delta.days < 1 or delta.days > _MAXORDINAL:
            raise OverflowError("result out of range")
        y, mo, d = _ord2ymd(int(delta.days))
        hh, rem = divmod(int(delta.seconds), 3600)
        mm, ss = divmod(rem, 60)
        return datetime(y, mo, d, hh, mm, ss, int(delta.microseconds))

    @overload
    def __sub__(self, other: "datetime") -> timedelta:
        secs1 = int(self.second) + int(self.minute) * 60 + int(self.hour) * 3600
        secs2 = int(other.second) + int(other.minute) * 60 + int(other.hour) * 3600
        return timedelta(days=self.toordinal() - other.toordinal(),
                         seconds=secs1 - secs2,
                         microseconds=int(self.microsecond) - int(other.microsecond))

    @overload
    def __sub__(self, other: timedelta) -> "datetime":
        return self + (-other)
