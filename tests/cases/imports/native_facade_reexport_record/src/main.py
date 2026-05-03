# Native_module facade re-exporting a *record* (not just a variable).
# Exercises the can_reexport extension to native_module facades for
# records: the consumer should see Counter as if it lived directly in the
# facade's surface.
from pkg import Counter
from tpy import Int32


def use(c: Counter) -> Int32:
    return c.n


def main() -> None:
    print(use(Counter(Int32(42))))


main()
