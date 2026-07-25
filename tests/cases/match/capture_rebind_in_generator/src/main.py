# A rebound match capture inside a generator arm hoists into the resumable
# frame (a frame field, not a block-scoped local), so rebinds before and after
# a suspension both land on the same slot and the value survives the yield.
from typing import Iterator


class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def counts(a: Cat) -> Iterator[int]:
    match a:
        case Cat(lives=v):
            v = v + 1    # tpyc: ok
            yield v      # 6
            v = v + 10   # rebind after the suspension, same slot
            yield v      # 16


def main() -> None:
    for x in counts(Cat(5)):
        print(x)


main()
