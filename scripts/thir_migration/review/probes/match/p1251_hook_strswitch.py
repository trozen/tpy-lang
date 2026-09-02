from typing import Iterator

def gen(s: str) -> Iterator[str]:
    match s:
        case "a":
            yield "1"
        case "b":
            yield "2"
        case "c":
            yield "3"
        case "d":
            yield "4"
        case "e":
            yield "5"
        case _:
            yield "0"

def main() -> None:
    for s in gen("a"):
        print(s)

main()
