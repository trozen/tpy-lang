from tpy import Int32
from typing import Iterator
def g(s: str, n: Int32) -> Iterator[str]:
    s = s + "!"
    for i in range(n):
        yield s
def main() -> None:
    for v in g("a", 2):
        print(v)
main()
