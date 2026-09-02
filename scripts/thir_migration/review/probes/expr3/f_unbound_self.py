from typing import Iterator
from tpy import Int32
class Counter:
    value: Int32
class Combined(Counter):
    def __init__(self) -> None:
        Counter.value = 3
    def walk(self) -> Iterator[Int32]:
        i = 0
        while i < Counter.value:
            yield i
            i = i + 1
def main() -> None:
    c = Combined()
    for x in c.walk():
        print(x)
main()
