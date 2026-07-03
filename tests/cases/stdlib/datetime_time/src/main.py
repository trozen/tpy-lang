# datetime.time v2: construction/validation, attributes, isoformat/str/repr
# (CPython's trailing-zero trimming), comparisons, hashability. Byte-compared
# against real CPython datetime.
from datetime import time


def main() -> None:
    t = time(14, 30, 5)
    print(t.isoformat())            # 14:30:05
    print(str(t))                   # 14:30:05
    print(repr(t))                  # datetime.time(14, 30, 5)
    print(t.hour, t.minute, t.second, t.microsecond)  # 14 30 5 0
    print(time())                   # 00:00:00 (all defaults)
    print(repr(time()))             # datetime.time(0, 0)
    print(repr(time(7)))            # datetime.time(7, 0)
    print(repr(time(7, 8)))         # datetime.time(7, 8)
    print(repr(time(7, 8, 0, 9)))   # datetime.time(7, 8, 0, 9) (us keeps zero s)
    print(time(1, 2, 3, 400000))    # 01:02:03.400000
    print(time(23, 59, 59, 999999)) # 23:59:59.999999
    print(time(1, 2) < time(1, 3))  # True
    print(time(1, 2) < time(1, 2, 0, 1))  # True (microsecond tiebreak)
    print(time(5) == time(5, 0, 0, 0))    # True
    print(time(5) != time(5))       # False
    print(time(9, 30) >= time(9, 29, 59, 999999))  # True
    seen = {time(14, 30): "a"}      # frozen -> hashable dict key
    print(seen[time(14, 30)])       # a
    print(time(0) in {time(0), time(1)})  # True (set membership)
    try:
        bad = time(24)              # hour out of range
        print("no error")
    except ValueError:
        print("caught hour")
    try:
        bad2 = time(0, 60)          # minute out of range
        print("no error")
    except ValueError:
        print("caught minute")
    try:
        bad3 = time(0, 0, 60)       # second out of range
        print("no error")
    except ValueError:
        print("caught second")
    try:
        bad4 = time(0, 0, 0, 1000000)   # microsecond out of range
        print("no error")
    except ValueError:
        print("caught microsecond")
    try:
        bad5 = time(-1)             # negative hour
        print("no error")
    except ValueError:
        print("caught negative")


main()
