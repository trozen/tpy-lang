from typing import Iterator

class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def gen(u: A | B) -> Iterator[int]:
    match u:
        case A(x=n) | B(x=n):
            yield 1
            yield n

def main() -> None:
    for v in gen(A(3)):
        print(v)

main()
