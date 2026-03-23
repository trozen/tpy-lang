# Error: yield expression type doesn't match Iterator[T]
from tpy import Int32
from typing import Iterator

def bad() -> Iterator[Int32]:
    yield "hello"  # tpyc: error(/Type mismatch in yield value/)

def main():
    pass

main()
