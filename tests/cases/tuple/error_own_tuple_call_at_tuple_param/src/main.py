# An owned tuple off a method call passed to a borrowed tuple parameter is refused,
# never left as a C++ build error (BUGS.md#own-tuple-call-at-borrow-tuple-param).
from tpy import int32


def take(p: tuple[list[str], int32]) -> None:
    print("take")


def main() -> None:
    stack: list[tuple[list[str], int32]] = [(["x"], 1)]
    # The popped tuple handed straight to the tuple parameter.
    take(stack.pop())  # tpyc: error(/not yet supported/)


main()
