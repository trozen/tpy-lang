# Generator with yield inside for-loop over container
from tpy import Int32
from typing import Iterator

def doubled(items: list[Int32]) -> Iterator[Int32]:
    for x in items:
        yield x * 2

def main():
    for x in doubled([1, 2, 3, 4, 5]):
        print(x)

main()
