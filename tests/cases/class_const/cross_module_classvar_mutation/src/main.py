# Mutable ClassVar across translation units: writes from one module hit the
# same `static inline` slot read from another. Exercises the C++17+ cross-TU
# guarantee that `static inline` gives the storage one address program-wide.
from counters import Counters, bump_from_other_module


def main() -> None:
    Counters.total = 1
    bump_from_other_module()
    Counters.total += 5
    print(Counters.total)


main()
