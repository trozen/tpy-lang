# Error: yield without a value is not supported
from tpy import Int32
from typing import Iterator

def bad() -> Iterator[Int32]:
    yield  # tpyc: error(/yield.*must have a value/)

def main():
    pass

main()
