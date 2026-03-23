# Complex generator: yield before + yield inside for-loop over range
from tpy import Int32
from typing import Iterator

def squares_plus(n: Int32) -> Iterator[Int32]:
    yield -1
    for i in range(n):
        yield i * i

def main():
    for x in squares_plus(5):
        print(x)

main()
