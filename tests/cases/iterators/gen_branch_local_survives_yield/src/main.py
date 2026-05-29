# Regression: a local first-assigned inside an if/match branch (the branch
# itself carries no suspension) in a resumable generator must resolve to its
# frame field, not a shadowing C++ local pre-declared at the branch site --
# otherwise the value is lost across a later yield. See BUGS.md.
from typing import Iterator


def gen_if(n: int) -> Iterator[int]:
    if n == 0:
        r = 100
    else:
        r = n + 1
    yield r
    yield r + 1


def gen_match(n: int) -> Iterator[int]:
    match n:
        case 0:
            r = 100
        case v:
            r = v + 1
    yield r
    yield r + 1


def main() -> None:
    for y in gen_if(5):
        print(y)
    for y in gen_match(5):
        print(y)
    for y in gen_if(0):
        print(y)
    for y in gen_match(0):
        print(y)


main()
