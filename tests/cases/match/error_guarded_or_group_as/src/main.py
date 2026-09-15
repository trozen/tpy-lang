# A GUARDED or-group arm may not also carry the `as` binding. The guarded
# record chain composes an or-arm's alternatives and its guard into one block
# condition (`(conds) && guard`), so the guard would run ahead of the binding
# line it reads. The unguarded spelling, and a guard on a NEIGHBOURING arm,
# both bind fine -- match/or_group_as_binding.
from tpy import int32


class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


def f(c: Cat, flag: bool) -> int32:
    match c:  # tpyc: error(/stmt\.match/)
        case Cat(lives=1) | Cat(lives=2) as q if flag:
            return q.lives
        case _:
            return 0


def main() -> None:
    print(f(Cat(1), True))


main()
