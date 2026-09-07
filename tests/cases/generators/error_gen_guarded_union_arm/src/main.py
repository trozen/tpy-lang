# A generator matching a union with a GUARDED class arm: the guard makes the
# arm's source index disagree with its variant index, so the resume env
# cannot name the extraction alias.
from typing import Iterator


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


def pick(a: Dog | Cat, flag: bool) -> Iterator[str]:  # tpyc: error(/res\.match_strategy/)
    # The guard on the class arm is the subject.
    match a:
        case Dog() if flag:
            yield "d"
            yield a.sound()
        case _:
            yield "o"


def main() -> None:
    for s in pick(Dog(), True):
        print(s)


main()
