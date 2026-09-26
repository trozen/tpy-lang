# Pins BUGS.md#tuple-param-borrow-granularity-rejects-valid: the result views
# the `A` element's field, but the whole tuple parameter is blamed for it.
from tpy import StrView


class A:
    def __init__(self, name: str) -> None:
        self.name = name


def nm(t: tuple[str, A]) -> StrView:
    return t[1].name


def head(s: str, a: A) -> StrView:
    # a truly temporary `str` element is blamed; the scalar twin nm1(s + "!", a) is accepted
    return nm((s + "!", a))  # tpyc: error(/Cannot return StrView referencing a local or temporary/)


def main() -> None:
    print(head("s", A("abcdefghijklmnopqrstuvwxyz0123")))


main()
