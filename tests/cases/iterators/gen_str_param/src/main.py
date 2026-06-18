# A generator with a str parameter (captured owned as std::string in the frame)
from typing import Iterator

def greetings(name: str) -> Iterator[str]:
    yield "hello " + name
    yield "goodbye " + name

def main() -> None:
    for g in greetings("world"):
        print(g)

main()
