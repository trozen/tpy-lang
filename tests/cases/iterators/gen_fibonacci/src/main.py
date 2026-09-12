# Generator with mutable state: fibonacci sequence
from tpy import int32
from typing import Iterator

def fibonacci(n: int32) -> Iterator[int32]:
    a: int32 = 0
    b: int32 = 1
    count: int32 = 0
    while count < n:
        yield a
        temp: int32 = a
        a = b
        b = temp + b
        count += 1

def main():
    for x in fibonacci(8):
        print(x)

main()
