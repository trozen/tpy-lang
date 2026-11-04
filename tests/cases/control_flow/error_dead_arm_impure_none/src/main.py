# `is None` on a never-None operand that runs code stays an error, as a
# test too (here a ternary's; an `if` test is refused the same way): a test
# that runs code stays as written, and that one has no lowering
# (BUGS.md#impure-never-none-test-rejects).
from tpy import Own


class A:
    def __init__(self, n: int) -> None:
        self.n = n


def make() -> Own[A]:
    return A(1)


def main() -> None:
    s = "none" if make() is None else "some"  # tpyc: error(/the operand is a 'A', which is never None/)
    print(s)


main()
