from typing import Iterator
from tpy import int32
class Counter:
    value: int32
class Combined(Counter):
    def __init__(self) -> None:
        Counter.value = 3
    def walk(self) -> Iterator[int32]:
        i = 0
        while i < Counter.value:
            yield i
            i = i + 1
def main() -> None:
    c = Combined()
    for x in c.walk():
        print(x)
main()
