# Warning: generator borrows from temporary str (string_view into destroyed temp)
from typing import Iterator

def greetings(name: str) -> Iterator[str]:
    yield "hello " + name
    yield "goodbye " + name

def make_name() -> str:
    return "wor" + "ld"

def main() -> None:
    g = greetings(make_name())  # tpyc: warning(/borrows from temporary/)
    for msg in greetings(make_name()):  # tpyc: warning(/borrows from temporary/)
        pass
    for msg in greetings("world"):  # tpyc: ok (string literal is static)
        print(msg)

main()
