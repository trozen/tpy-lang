# Generator with mutable state: fibonacci sequence
from tpy import Int32
from typing import Iterator

def fibonacci(n: Int32) -> Iterator[Int32]:
    a: Int32 = 0
    b: Int32 = 1
    count: Int32 = 0
    while count < n:
        yield a
        temp: Int32 = a
        a = b
        b = temp + b
        count += 1

def main():
    for x in fibonacci(8):
        print(x)

main()
