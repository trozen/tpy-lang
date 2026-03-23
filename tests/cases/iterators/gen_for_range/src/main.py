# Generator with yield inside for-loop over range
from tpy import Int32
from typing import Iterator

def squares(n: Int32) -> Iterator[Int32]:
    for i in range(n):
        yield i * i

def main():
    for x in squares(5):
        print(x)

main()
