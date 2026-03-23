# Generator with multiple sequential yield points
from tpy import Int32
from typing import Iterator

def triple() -> Iterator[Int32]:
    yield 10
    yield 20
    yield 30

def main():
    for x in triple():
        print(x)

main()
