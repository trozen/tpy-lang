# A nested def in a plain function mutating a CAPTURED reference-type param:
# the mutation fact must reach the enclosing function's signature (non-const
# param) and the caller's list must observe the appends.
from tpy import int32


def fill(xs: list[int32], v: int32) -> None:
    def add() -> None:
        xs.append(v)

    add()
    add()


def main() -> None:
    xs: list[int32] = []
    fill(xs, 7)
    print(xs)


main()
