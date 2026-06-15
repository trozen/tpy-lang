# Subscript-read on a generator-yielded Own[dict]/Own[list] loop var routes
# through __getitem__ (const-correct, index-normalizing, bounds/key-checked).
from typing import Iterator
from tpy import Own, readonly


def dicts() -> Iterator[Own[dict[str, str]]]:
    a: dict[str, str] = {}
    a["k"] = "v1"
    yield a
    b: dict[str, str] = {}
    b["k"] = "v2"
    yield b


def lists() -> Iterator[Own[list[int]]]:
    a: list[int] = [10, 20, 30]
    yield a


def read_dicts() -> None:
    for d in dicts():
        print(d["k"])


def read_list_normalized() -> None:
    for xs in lists():
        print(xs[-1])    # negative index must normalize -> 30
        print(xs[0])     # 10


def read_ro_dict(d: readonly[dict[str, str]]) -> str:
    # Inverse: a readonly dict param read must keep working.
    return d["k"]


def list_oob_raises() -> None:
    # The __getitem__ route bounds-checks; raw operator[] was UB.
    for xs in lists():
        try:
            print(xs[99])
            print("FAIL: no IndexError")
        except IndexError:
            print("got IndexError")


def dict_missing_raises() -> None:
    # The __getitem__ route raises KeyError; raw operator[] would default-insert.
    for d in dicts():
        try:
            print(d["absent"])
            print("FAIL: no KeyError")
        except KeyError:
            print("got KeyError")
        break


def main() -> None:
    read_dicts()
    print("---")
    read_list_normalized()
    print("---")
    m: dict[str, str] = {}
    m["k"] = "ro"
    print(read_ro_dict(m))
    print("---")
    list_oob_raises()
    print("---")
    dict_missing_raises()


main()
