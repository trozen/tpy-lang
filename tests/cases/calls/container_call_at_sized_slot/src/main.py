# An owning container-returning call used directly as `len()`'s argument:
# the rvalue container is admitted whole at the sized slot.
from tpy import Int32, Own


def empty_set() -> Own[set[Int32]]:
    return set()


def f() -> None:
    # The container rvalue is the argument, with no local in between.
    print(len(empty_set()))


def main() -> None:
    f()


main()
