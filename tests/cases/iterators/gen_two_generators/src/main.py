# Two complex generators in the same module (state numbering isolation)
from tpy import Int32
from typing import Iterator

def gen_a() -> Iterator[Int32]:
    yield 1
    yield 2

def gen_b() -> Iterator[Int32]:
    yield 10
    yield 20
    yield 30

def main():
    for x in gen_a():
        print(x)
    for x in gen_b():
        print(x)

main()
