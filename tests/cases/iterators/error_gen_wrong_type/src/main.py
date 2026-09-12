# Error: generator function without Iterator[T] return type
from tpy import int32

def bad() -> int32:  # tpyc: error(/Generator.*Iterator/)
    yield 1

def main():
    pass

main()
