# Peephole-generator while conditions: anonymous temps re-evaluate per pull in
# the lambda's loop head; a walrus pre-decl lands at lambda scope.
from typing import Iterator


def eat(xs: list[int]) -> int:
    n = len(xs)
    if n > 0:
        xs.pop()
    return n


def fresh_each_pull() -> Iterator[int]:
    # A fresh [1, 2] per condition evaluation keeps eat() returning 2
    # forever; the caller's guard bounds the pulls.
    while eat([1, 2]) > 1:
        yield 1


def walrus_gen(limit: int) -> Iterator[int]:
    n = limit
    while (m := n) > 0:
        yield m
        n -= 1


def main():
    pulls = 0
    for _ in fresh_each_pull():
        pulls += 1
        if pulls >= 5:
            break
    print(pulls)
    for v in walrus_gen(3):
        print(v)


main()
