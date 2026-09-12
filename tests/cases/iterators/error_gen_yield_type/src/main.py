# Error: yield expression type doesn't match Iterator[T]
from tpy import int32
from typing import Iterator

def bad() -> Iterator[int32]:
    yield "hello"  # tpyc: error(/Type mismatch in yield value/)

def main():
    pass

main()
