from tpy import int32
from typing import Iterator
class C:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
def storage_relay(items: list[tuple[int32, C]]) -> Iterator[tuple[int32, C]]:
    for pair in items:
        yield pair
def main() -> None:
    xs: list[tuple[int32, C]] = [(1, C(5))]
    for t in storage_relay(xs):
        print(t[0])
main()
