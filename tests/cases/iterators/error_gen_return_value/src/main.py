# Error: return with value in generator function
from tpy import int32
from typing import Iterator

def bad() -> Iterator[int32]:
    yield 1
    return 42  # tpyc: error(/cannot use 'return' with a value/)

def main():
    pass

main()
