# datetime.datetime v2 (naive): construction/validation, combine,
# utcfromtimestamp (fixed timestamps -> deterministic), arithmetic with
# timedelta and datetime, comparisons, weekday/toordinal, isoformat/str/repr
# trimming, hashability. Byte-compared against real CPython datetime.
# (dt.date()/dt.time() accessors are covered by stdlib/datetime_accessors.)
from datetime import date, time, datetime, timedelta


def main() -> None:
    dt = datetime(2026, 7, 3, 14, 30, 5)
    print(dt.isoformat())           # 2026-07-03T14:30:05
    print(dt.isoformat(" "))        # 2026-07-03 14:30:05
    print(str(dt))                  # 2026-07-03 14:30:05
    print(repr(dt))                 # datetime.datetime(2026, 7, 3, 14, 30, 5)
    print(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond)
    print(datetime(2026, 7, 3))     # 2026-07-03 00:00:00
    print(repr(datetime(2026, 7, 3)))            # datetime.datetime(2026, 7, 3, 0, 0)
    print(repr(datetime(2026, 7, 3, 0, 0, 9)))   # keeps seconds
    print(repr(datetime(2026, 7, 3, 0, 0, 0, 9)))  # us keeps zero seconds
    print(datetime(2026, 1, 2, 3, 4, 5, 678901).isoformat())
    print(dt.weekday(), dt.isoweekday(), dt.toordinal())  # 4 5 739800

    print(datetime.combine(date(2021, 3, 5), time(9, 8, 7)))
    print(datetime.combine(date(2020, 2, 29), time(23, 59, 59, 999999)))

    print(datetime.utcfromtimestamp(0))             # 1970-01-01 00:00:00
    print(datetime.utcfromtimestamp(1751551805.5))  # fractional seconds
    print(datetime.utcfromtimestamp(-1.25))         # pre-epoch, negative frac
    print(datetime.utcfromtimestamp(86399))         # integer-valued float
    print(datetime.utcfromtimestamp(253402300799.5))  # 9999-12-31 23:59:59.500000 (max day)

    print(dt + timedelta(hours=10))                 # crosses midnight
    print(dt + timedelta(days=-1, microseconds=1))
    print(dt - timedelta(days=2, hours=15))
    print(dt - datetime(2026, 7, 1))                # -> timedelta
    print(datetime(2026, 7, 1) - dt)                # negative timedelta
    print(dt - datetime(2026, 7, 3, 14, 30, 4, 999999))  # 1 us
    print((datetime(2026, 12, 31, 23, 59, 59, 999999) + timedelta(microseconds=1)).isoformat())

    print(dt > datetime(2026, 7, 1))    # True
    print(dt <= dt, dt == dt, dt != dt) # True True False
    print(dt >= dt, datetime(2026, 7, 1) >= dt)  # True False
    print(datetime(2026, 7, 3, 14, 30, 5, 1) > dt)  # True (us tiebreak)
    print(dt < datetime(2026, 7, 3, 14, 30, 6))    # True
    seen = {datetime(2026, 7, 3): "a"}  # frozen -> hashable dict key
    print(seen[datetime(2026, 7, 3)])   # a

    try:
        bad = datetime(2026, 2, 30)     # invalid date part
        print("no error")
    except ValueError:
        print("caught day")
    try:
        bad2 = datetime(2026, 7, 3, 24)  # invalid time part
        print("no error")
    except ValueError:
        print("caught hour")
    try:
        bad3 = datetime(9999, 12, 31, 23, 59, 59) + timedelta(seconds=1)
        print("no error")
    except OverflowError:
        print("caught OverflowError")
    try:
        bad4 = datetime(1, 1, 1) - timedelta(microseconds=1)  # below year 1
        print("no error")
    except OverflowError:
        print("caught underflow")
    try:
        bad5 = datetime.utcfromtimestamp(300000000000)  # beyond year 9999
        print("no error")
    except ValueError:
        # CPython raises ValueError (year out of range), not OverflowError
        print("caught timestamp range")
    try:
        bad6 = datetime.utcfromtimestamp(-62135596801)  # before year 1
        print("no error")
    except ValueError:
        print("caught pre-year-1")
    try:
        bad7 = datetime.utcfromtimestamp(1e20)  # beyond 64-bit time_t
        print("no error")
    except OverflowError:
        print("caught utc time_t overflow")
    try:
        bad8 = datetime.fromtimestamp(1e20)  # local path, same bound
        print("no error")
    except OverflowError:
        print("caught local time_t overflow")
    try:
        # ~745 days below the year-1 boundary: no real-world tz offset can
        # shift it into range, so both toolchains raise on every host
        # (macOS localtime behavior for year <1 unverified from Linux --
        # if the cpy phase diverges there, this is the line to revisit).
        bad9 = datetime.fromtimestamp(-62200000000.0)
        print("no error")
    except ValueError:
        print("caught local pre-year-1")


main()
