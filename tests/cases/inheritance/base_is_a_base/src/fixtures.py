# Builtin-base subclasses with no subclass of their own, and the helpers
# that take them, shared by main.py and several parts.
from typing import Iterable
from tpy import int32


class DSub(dict[str, int32]):
    def __init__(self) -> None:
        pass


class LSub(list[int32]):
    def __init__(self) -> None:
        pass


class SSub(set[int32]):
    def __init__(self) -> None:
        pass


def put_dict(m: dict[str, int32], k: str) -> int32:
    m[k] = 7
    return len(m)


def put_list(xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


def put_set(xs: set[int32]) -> int32:
    xs.add(9)
    return len(xs)


def total(it: Iterable[int32]) -> int32:
    n = 0
    for x in it:
        n += x
    return n
