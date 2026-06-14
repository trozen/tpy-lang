# Generator iterating d.values() across yields: the loop var aliases the
# dict's stored list, so mutations inside the generator reach the dict.
from tpy import Int32
from typing import Iterator


def bump(d: dict[str, list[Int32]]) -> Iterator[Int32]:
    for v in d.values():
        v.append(9)
        yield len(v)


def main():
    d: dict[str, list[Int32]] = {}
    d["a"] = [1]
    d["b"] = [2, 3]
    for n in bump(d):
        print(n)
    print(d["a"], d["b"])


main()
