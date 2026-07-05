# tpy: cpp_namespace("tpystd::_datetime_cal")
# Calendar math for the datetime module: proleptic-Gregorian ordinals,
# leap years, ISO week calendar, and the C-locale name tables shared by
# the formatting and parsing engines. Pure functions over int; no class
# dependencies (leaf module).
# Not for direct user import; the public surface is `datetime`.

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


# C-locale name tables (CPython delegates strftime to the platform in the
# C locale under test conditions; TPy hardcodes English names -- LC_TIME is
# never consulted, a documented divergence).
_DAY_ABBR: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_FULL: list[str] = ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
_MONTH_ABBR: list[str] = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_FULL: list[str] = ["", "January", "February", "March", "April",
                          "May", "June", "July", "August", "September",
                          "October", "November", "December"]


def _isoweek1monday(year: int) -> int:
    # Ordinal of the Monday starting ISO week 1 (the week with the first
    # Thursday) of this year.
    firstday = _ymd2ord(year, 1, 1)
    firstweekday = (firstday + 6) % 7
    week1monday = firstday - firstweekday
    if firstweekday > 3:
        week1monday = week1monday + 7
    return week1monday


def _iso_calendar(y: int, mo: int, d: int) -> tuple[int, int, int]:
    # CPython date.isocalendar(): (iso_year, iso_week, iso_weekday).
    week1monday = _isoweek1monday(y)
    today = _ymd2ord(y, mo, d)
    week, day = divmod(today - week1monday, 7)
    if week < 0:
        y = y - 1
        week1monday = _isoweek1monday(y)
        week, day = divmod(today - week1monday, 7)
    elif week >= 52 and today >= _isoweek1monday(y + 1):
        y = y + 1
        week = 0
    return (y, week + 1, day + 1)



def _isoweek_to_gregorian(year: int, week: int,
                          day: int) -> tuple[int, int, int]:
    # Port of _pydatetime._isoweek_to_gregorian (date.fromisocalendar core).
    if year < 1 or year > 9999:
        raise ValueError(f"Year is out of range: {year}")
    if week < 1 or week > 53:
        raise ValueError(f"Invalid week: {week}")
    if week == 53:
        # 53-week ISO years start on Thursday, or Wednesday when leap.
        first_weekday = _ymd2ord(year, 1, 1) % 7
        if not (first_weekday == 4 or (first_weekday == 3
                                       and _is_leap(year))):
            raise ValueError(f"Invalid week: {week}")
    if day < 1 or day > 7:
        raise ValueError(f"Invalid weekday: {day} (range is [1, 7])")
    ord_day = _isoweek1monday(year) + (week - 1) * 7 + (day - 1)
    if ord_day < 1 or ord_day > _MAXORDINAL:
        raise ValueError("ISO date out of range")
    return _ord2ymd(ord_day)

