# An `items()` unpack whose value target is a value-Optional, narrowed and
# then yielded: the yield derefs the narrowed target.
from typing import Iterator
from tpy import Int32


def g_items(d: dict[str, Int32 | None]) -> Iterator[Int32]:
    for k, v in d.items():
        # `v` is a value-optional unpack target narrowed before the yield.
        if v is not None and len(k) > 0:
            yield v


def main() -> None:
    print(sum(g_items({"a": 1, "b": None, "c": 2})))


main()
