# A `while True` WITH a loop-level break can fall through, so the
# missing-return error still fires.
from tpy import Int32


def f(n: Int32) -> Int32:  # tpyc: error(/'f' can reach the end of the function without returning/)
    while True:
        if n > 0:
            return n
        break


def main() -> None:
    print(f(1))


main()
