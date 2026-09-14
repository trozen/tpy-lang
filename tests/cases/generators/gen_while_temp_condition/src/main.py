# Generator conditions carrying an anonymous argument temp: the temp
# re-evaluates per pull. In the resumable frame it lands inside the `case`
# block, so it is rebuilt on every re-entry.
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


# free generator, TWO yields (frame): the while-head temp is rebuilt on every
# re-entry, so eat() keeps seeing a fresh [1, 2].
def fresh_each_pull_framed() -> Iterator[int]:
    while eat([1, 2]) > 1:  # tpyc: ok
        yield 1
        yield 2


# An IF condition carrying the same temp -- the frame's Branch seam is shared
# by if and while heads, so the fresh list is rebuilt per loop iteration.
def if_cond_temp(n: int) -> Iterator[int]:
    for i in range(n):
        if eat([1, 2, 3]) > 2:  # tpyc: ok
            yield i
        yield -i


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

    framed = 0
    for _ in fresh_each_pull_framed():
        framed += 1
        if framed >= 5:
            break
    print("framed", framed)

    print("if_cond", list(if_cond_temp(2)))


main()
