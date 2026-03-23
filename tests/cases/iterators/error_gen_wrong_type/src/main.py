# Error: generator function without Iterator[T] return type
from tpy import Int32

def bad() -> Int32:  # tpyc: error(/Generator.*Iterator/)
    yield 1

def main():
    pass

main()
