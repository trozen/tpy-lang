# A non-None return type with a reachable end of body is rejected
# (falling off a non-void C++ function is UB; mypy rejects the same).
from tpy import int32


def f(n: int32) -> int32:  # tpyc: error(/'f' can reach the end of the function without returning/)
    if n > 0:
        return 1


def main() -> None:
    print(f(1))


main()
