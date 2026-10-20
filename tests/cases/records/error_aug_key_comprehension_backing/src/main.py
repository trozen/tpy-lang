# An augmented key viewing a list a comprehension builds cannot be evaluated
# once (the list would die first); binding the comprehension to a name can.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, Assignment.)
from tpy import StrView


def label() -> str:
    return " y "


def first(xs: list[str]) -> StrView:
    return xs[0]


def main() -> None:
    d = {" y ": 1}
    d[first([label() for i in range(1)])] += 1  # tpyc: error(/the subscript key here views a temporary that cannot be kept until the store/)
    print(d)


main()
