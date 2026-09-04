# A slice-ASSIGN bound that is a COMPOSITE over a retro-widened local: the
# narrow keys on the declared type, so the render would be ill-formed and the
# write side rejects it exactly as the slice READ side does.
from tpy import Int32, Own


def f() -> Own[list[Int32]]:
    xs = list(range(0, 12))
    n = 1
    n += 100000000000000000000
    xs[n + 1:] = [0]  # tpyc: error(/stmt\.assign:expr\.subscript/)
    return xs


def main() -> None:
    print(f())


main()
