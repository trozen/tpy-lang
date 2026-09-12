# Generator with yield inside for-loop over range
from tpy import int32
from typing import Iterator

def squares(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i * i

def main():
    for x in squares(5):
        print(x)

main()
