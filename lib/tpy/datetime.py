# tpy: cpp_namespace("tpystd::datetime")
# The datetime module: date / time / datetime / timedelta / timezone.
# See docs/DATETIME_DESIGN.md. Value-typed frozen dataclasses; calendar math
# and formatting are pure-TPy for byte-parity with CPython (the cpy test phase
# resolves the real CPython datetime, so this file is compiled only by TPy).
#
# v1: timedelta + date. Integer constructor args only (CPython also accepts
# float args + float * / /; deferred, see the roadmap).
from __future__ import annotations
from typing import overload
from dataclasses import dataclass
from tpy import Int32, ValueType

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
    def __floordiv__(self, other: timedelta) -> int: ...
    @overload
    def __floordiv__(self, other: int) -> timedelta: ...
    def __floordiv__(self, other: timedelta | int) -> int | timedelta:
        if isinstance(other, timedelta):
            return self._to_microseconds() // other._to_microseconds()
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


@dataclass(frozen=True, order=True)
class date(ValueType):
    year: Int32
    month: Int32
    day: Int32

    def __init__(self, year: int, month: int, day: int) -> None:
        if year < 1 or year > 9999:
            raise ValueError("year is out of range")
        if month < 1 or month > 12:
            raise ValueError("month must be in 1..12")
        if day < 1 or day > _days_in_month(year, month):
            raise ValueError("day is out of range for month")
        self.year = year
        self.month = month
        self.day = day

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
    def __sub__(self, other: "date") -> timedelta: ...
    @overload
    def __sub__(self, other: timedelta) -> "date": ...
    def __sub__(self, other: "date | timedelta") -> "timedelta | date":
        if isinstance(other, timedelta):
            o = self.toordinal() - other.days
            if o < 1 or o > _MAXORDINAL:
                raise OverflowError("result out of range")
            return date.fromordinal(o)
        return timedelta(days=self.toordinal() - other.toordinal())
