# A later arm's binding inside a nested block, of a name an earlier arm bound
# readonly, would share a const slot it writes through: refused, not ill-formed C++.
from tpy import readonly


def f(c: bool, k: int, ro: readonly[list[int]], xa: list[int]) -> None:
    if c:
        y = ro
        print(len(y))
        return
    else:
        if k > 0:
            y = xa
        else:
            y = xa
        # the write through the arm's own mutable binding
        y.append(9)  # tpyc: error(/not yet supported/)
        return


def main() -> None:
    a: list[int] = [0]
    b: list[int] = [0]
    f(False, 1, a, b)
    print(b)


main()
