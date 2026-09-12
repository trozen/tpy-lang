from typing import Iterator
from tpy import int32
def pair(n: int32) -> tuple[int32, int32]:
    return (n, n + 1)
def g(n: int32) -> Iterator[int32]:
    try:
        yield n
    finally:
        a, b = pair(n)
        print(a + b)
def main() -> None:
    for v in g(1):
        print(v)
main()
