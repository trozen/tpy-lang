# A module-level `def print` shadows the builtin for every body in the module:
# the call is a plain user call, not the builtin print form, and the user
# function is not a shape the caller position has an arm for. TPy rejects
# calling `print(n)` inside `record` today, once `print` is redefined.
from tpy import int32

seen: list[int32] = []


def print(n: int32) -> None:
    seen.append(n)


def record(n: int32) -> None:
    print(n)  # tpyc: error(/call\.builtin_special/)


def main() -> None:
    record(3)


main()
