from typing import Iterator

from tpy import dispatch, readonly


class Cat:
    def __init__(self, lives: int) -> None:
        self.lives = lives


class Dog:
    def __init__(self, age: int) -> None:
        self.age = age


def code(pet: Cat | Dog) -> int:
    if isinstance(pet, Dog):
        return pet.age
    return pet.lives


def bump(pet: Cat | Dog) -> None:
    if isinstance(pet, Dog):
        pet.age += 1


class K:
    @staticmethod
    def static_code(pet: Cat | Dog) -> int:
        if isinstance(pet, Dog):
            return pet.age
        return pet.lives

    @classmethod
    def cls_code(cls, pet: Cat | Dog) -> int:
        if isinstance(pet, Dog):
            return pet.age
        return pet.lives

    @staticmethod
    @dispatch
    def sov(x: str) -> int:
        return len(x)

    @staticmethod
    @dispatch
    def sov(x: bool) -> int:
        return 7


def ls(xs: list[int]) -> int:
    return len(xs)


def ls_mut(xs: list[int]) -> int:
    xs.append(9)
    return len(xs)


def dct(d: dict[str, int]) -> int:
    return len(d)


def total(xs: readonly[list[int]]) -> int:
    n = 0
    for x in xs:
        n += x
    return n


def request(url: str, auth: tuple[str, str] | None = None) -> int:
    return len(url) if auth is None else len(url) + len(auth[0])


@dispatch
def ov(x: str) -> int:
    return len(x)


@dispatch
def ov(x: bool) -> int:
    return 7


def gen_tags(prefix: str) -> Iterator[str]:
    yield prefix + "1"
    yield prefix + "2"


def gen_sizes(data: bytes) -> Iterator[int]:
    yield len(data)


def seq(xs: list[int], y: int) -> int:
    return len(xs) + y
