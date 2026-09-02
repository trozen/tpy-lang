from typing import Iterator
from tpy import Int32
def pair(n: Int32) -> tuple[Int32, Int32]:
    return (n, n + 1)
def g(n: Int32) -> Iterator[Int32]:
    try:
        yield n
    finally:
        a, b = pair(n)
        print(a + b)
def main() -> None:
    for v in g(1):
        print(v)
main()
