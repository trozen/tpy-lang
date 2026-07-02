# datetime.date v1: construction/validation, attributes, weekday/isoweekday,
# toordinal/fromordinal, isoformat/str/repr, comparisons, date +/- timedelta,
# date - date. Byte-compared against real CPython datetime.
from datetime import date, timedelta


def main() -> None:
    d = date(2021, 3, 5)
    print(d.isoformat())            # 2021-03-05
    print(str(d))                   # 2021-03-05
    print(repr(d))                  # datetime.date(2021, 3, 5)
    print(d.year, d.month, d.day)   # 2021 3 5
    print(d.weekday())              # 4
    print(d.isoweekday())           # 5
    print(d.toordinal())            # 737854
    print(date.fromordinal(737854).isoformat())    # 2021-03-05
    print(date(2020, 2, 29).isoformat())            # 2020-02-29
    print(d < date(2021, 3, 8))     # True
    print(d == date(2021, 3, 5))    # True
    seen = {date(2021, 3, 5): "a"}                  # frozen -> hashable dict key
    print(seen[date(2021, 3, 5)])   # a
    print(d in {date(2020, 1, 1), date(2021, 3, 5)})  # True (set membership)
    print(date(2021, 3, 8) - d)     # 3 days, 0:00:00
    print((d + timedelta(days=10)).isoformat())     # 2021-03-15
    print((d - timedelta(days=10)).isoformat())     # 2021-02-23
    print((date(2021, 12, 31) + timedelta(days=1)).isoformat())  # 2022-01-01
    print(date(2000, 2, 29).isoformat())            # 2000-02-29 (leap century)
    try:
        bad = date(2021, 2, 30)                     # day out of range
        print("no error")
    except ValueError:
        print("caught day")
    try:
        bad2 = date(10000, 1, 1)                    # year out of range
        print("no error")
    except ValueError:
        print("caught year")
    try:
        bad3 = date(2021, 13, 1)                    # month out of range
        print("no error")
    except ValueError:
        print("caught month")
    try:
        bad4 = date(1900, 2, 29)                    # 1900 is NOT a leap year
        print("no error")
    except ValueError:
        print("caught non-leap-century")
    try:
        bad5 = date(9999, 12, 31) + timedelta(days=1)   # ordinal overflow
        print("no error")
    except OverflowError:
        print("caught OverflowError")


main()
