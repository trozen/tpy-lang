# A nested match reusing an outer arm's capture name inside a GENERATOR: the
# hoisted slot lives in the resumable frame, so the inner bind survives the
# suspension between the two yields and is what flows out afterwards.
from typing import Iterator


class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def counts(a: Cat, b: Cat) -> Iterator[int]:
    match a:
        case Cat(lives=v):
            yield v          # 1 -- the outer bind
            match b:
                case Cat(lives=v):   # tpyc: ok -- rebinds across the suspension
                    pass
            yield v          # 2 -- the inner bind, read after resuming


def main() -> None:
    for x in counts(Cat(1), Cat(2)):
        print(x)


main()
