# A BigInt membership needle against a fixed-int set answers by VALUE: an
# out-of-declared-width needle is simply absent (False, like CPython) --
# never a range panic.
from tpy import int32, uint32


def main():
    s: set[int32] = {1, 2}
    k: int = 2
    print(k in s)
    big: int = 1099511627776  # 2**40
    print(big in s)
    print(big not in s)
    neg: int = -1099511627776  # -(2**40)
    print(neg in s)
    su: set[uint32] = {7}
    seven: int = 7
    print(seven in su)
    print(neg in su)


main()
