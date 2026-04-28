# Class constants live separately from instance fields, so @dataclass-style
# synthesized __init__ does not include them.
from dataclasses import dataclass
from typing import Final
from tpy import Int32


@dataclass
class Counter:
    count: Int32
    label: str
    DEFAULT_STEP: Final[Int32] = 1


def main() -> None:
    c = Counter(count=Int32(0), label="hits")
    print(c.count)
    print(c.label)
    print(Counter.DEFAULT_STEP)


main()
