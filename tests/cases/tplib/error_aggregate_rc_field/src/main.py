# Aggregate with an Rc[T] field: hits the @nocopy + has_del branch of
# `_is_default_constructible` (distinct from the required-arg __init__ branch).
# Without this guard, zero-arg construction would compile and C++ would
# implicitly delete the synthesized ctor.
from tpy import Int32
from tplib.rc import Rc


class Counter:
    x: Int32 = 0


class Holder:
    shared: Rc[Counter]


def main() -> None:
    h = Holder()  # tpyc: error(/Holder\(\).*field 'shared'/)


main()
