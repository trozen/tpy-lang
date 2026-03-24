# Test complex generator with str parameter (stored in struct as string_view)
from typing import Iterator

def greetings(name: str) -> Iterator[str]:
    yield "hello " + name
    yield "goodbye " + name

def main() -> None:
    for g in greetings("world"):
        print(g)

main()
