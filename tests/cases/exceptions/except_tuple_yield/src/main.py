# `yield` inside an `except (A, B):` body. Each expanded arm becomes its own
# resumable state, so a suspension inside the duplicated body must resume into
# the arm that caught -- a state-machine mix-up here would be a runtime
# divergence the generated-code snapshot alone would not catch.
from typing import Iterator


class AErr(Exception):
    pass


class BErr(Exception):
    pass


def boom(which: int) -> None:
    if which == 0:
        raise AErr()
    raise BErr()


def gen() -> Iterator[int]:
    for i in range(2):
        try:
            boom(i)
            yield -1
        except (AErr, BErr):  # tpyc: ok
            yield i
            yield i * 100


def main() -> None:
    for v in gen():
        print(v)


main()
