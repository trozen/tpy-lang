from tpy import Int32
from typing import Iterator
class C:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def storage_relay(items: list[tuple[Int32, C]]) -> Iterator[tuple[Int32, C]]:
    for pair in items:
        yield pair
def main() -> None:
    xs: list[tuple[Int32, C]] = [(1, C(5))]
    for t in storage_relay(xs):
        print(t[0])
main()
