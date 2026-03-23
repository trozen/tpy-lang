# Error: return with value in generator function
from tpy import Int32
from typing import Iterator

def bad() -> Iterator[Int32]:
    yield 1
    return 42  # tpyc: error(/cannot use 'return' with a value/)

def main():
    pass

main()
