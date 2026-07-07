# timedelta float operators: `td / number` and `td * float` / `td / float` with
# round-half-to-even, byte-parity with CPython. Float operands reduce to an exact
# rational (float.as_integer_ratio) so large timedeltas past 2**53 us round
# correctly rather than through a lossy double. (Float *constructor* components
# like timedelta(hours=1.5) are intentionally rejected -- use integer components
# or these operators.)
from datetime import timedelta


def main() -> None:
    # division by a number -> timedelta (round-half-to-even), incl. negatives
    print(timedelta(hours=1) / 3)
    print(timedelta(seconds=1) / 3)
    print(timedelta(hours=1) / 2.5)
    print(timedelta(seconds=1) / -3)
    print(timedelta(seconds=1) / -2.5)
    # exact-half remainders: round-half-to-EVEN (not half-up)
    print(timedelta(microseconds=1) / 2)   # 0.5 -> 0 (even)
    print(timedelta(microseconds=3) / 2)   # 1.5 -> 2 (even)
    print(timedelta(microseconds=5) / 2)   # 2.5 -> 2 (even)
    # multiplication by a float
    print(timedelta(minutes=10) * 1.5)
    print(1.5 * timedelta(minutes=10))
    print(timedelta(seconds=1) * -0.5)

    # division by zero raises ZeroDivisionError (int and float divisor)
    try:
        timedelta(seconds=1) / 0
    except ZeroDivisionError:
        print("zero-div int")
    try:
        timedelta(seconds=1) / 0.0
    except ZeroDivisionError:
        print("zero-div float")

    # exactness for a large timedelta (past 2**53 us): the rational path matches
    # CPython where a double approximation would drift.
    big = timedelta(days=100000000)
    print((big * 1.5) // timedelta(microseconds=1))
    print((big / 3.0) // timedelta(microseconds=1))
    print((big / 7) // timedelta(microseconds=1))

    # integer surface unchanged
    print(timedelta(hours=1) * 3)
    print(timedelta(days=1) // 2)
    print(timedelta(hours=1) / timedelta(minutes=30))


main()
