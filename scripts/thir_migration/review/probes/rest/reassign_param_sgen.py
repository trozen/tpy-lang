from tpy import int32
from typing import Iterator
def g(s: str, n: int32) -> Iterator[str]:
    s = s + "!"
    for i in range(n):
        yield s
def main() -> None:
    for v in g("a", 2):
        print(v)
main()
