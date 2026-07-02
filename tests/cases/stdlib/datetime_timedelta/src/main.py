# datetime.timedelta v1 (integer surface): construction/normalization,
# arithmetic (+ - unary *int //int //td /td %td), comparisons, abs,
# total_seconds, repr/str. Byte-compared against real CPython datetime.
from datetime import timedelta


def main() -> None:
    print(timedelta(hours=25, minutes=30))              # 1 day, 1:30:00
    print(repr(timedelta(hours=25, minutes=30)))        # datetime.timedelta(days=1, seconds=5400)
    print(timedelta(days=1, seconds=30))                # 1 day, 0:00:30
    print(repr(timedelta(days=1, seconds=30)))          # datetime.timedelta(days=1, seconds=30)
    print(repr(timedelta()))                            # datetime.timedelta(0)
    print(timedelta())                                  # 0:00:00
    print(timedelta(days=-1))                           # -1 day, 0:00:00
    print(repr(timedelta(microseconds=-1)))             # datetime.timedelta(days=-1, seconds=86399, microseconds=999999)
    print(timedelta(seconds=-1))                        # -1 day, 23:59:59
    print(timedelta(days=2, hours=6) + timedelta(hours=20))  # 3 days, 2:00:00
    print(timedelta(days=1) - timedelta(hours=1))       # 23:00:00
    print(-timedelta(days=1, hours=1))                  # -2 days, 23:00:00
    print(abs(timedelta(days=-2)))                      # 2 days, 0:00:00
    print(timedelta(hours=1) * 3)                       # 3:00:00
    print(4 * timedelta(minutes=15))                    # 1:00:00
    print(timedelta(hours=1) / timedelta(minutes=30))   # 2.0
    print(timedelta(hours=1) // timedelta(minutes=25))  # 2
    print(timedelta(hours=1) // 2)                      # 0:30:00
    print(timedelta(hours=1) % timedelta(minutes=25))   # 0:10:00
    print(timedelta(hours=1).total_seconds())           # 3600.0
    print(timedelta(days=1) > timedelta(hours=23))      # True
    print(timedelta(0) == timedelta(seconds=0))         # True
    print(bool(timedelta(0)), bool(timedelta(seconds=1)))  # False True
    # frozen -> hashable; normalized equal values hash equal (60min == 1h)
    print(timedelta(minutes=60) in {timedelta(hours=1), timedelta(minutes=30)})  # True
    try:
        bad = timedelta(days=10**9)                     # > 999999999 max
        print("no error")
    except OverflowError:
        print("caught OverflowError")


main()
