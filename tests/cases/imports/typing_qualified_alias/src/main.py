# Test aliased module with qualified access (import typing as t)
import typing as t
from tpy import int32

def safe_inc(x: t.Optional[int32]) -> int32:
    if x is not None:
        return x + int32(1)
    return int32(0)

class Greetable(t.Protocol):
    def greet(self) -> str: ...

class Person:
    name: str
    def __init__(self, n: str):
        self.name = n
    def greet(self) -> str:
        return self.name

def hello(g: Greetable) -> None:
    print(g.greet())

def main():
    print(safe_inc(int32(9)))
    print(safe_inc(None))
    hello(Person("Alice"))

main()
