# A module-level `def print` shadows the builtin for every body in the module:
# the call is a plain user call, not the builtin print form, and the user
# function is not a shape the caller position has an arm for. TPy rejects
# calling `print(n)` inside `record` today, once `print` is redefined.
from tpy import Int32

seen: list[Int32] = []


def print(n: Int32) -> None:
    seen.append(n)


def record(n: Int32) -> None:
    print(n)  # tpyc: error(/call\.builtin_special/)


def main() -> None:
    record(3)


main()
