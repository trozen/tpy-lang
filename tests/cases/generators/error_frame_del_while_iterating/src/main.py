# A generator is not deleted inside a `for` over it: the loop still runs it,
# and `del` closes it in place (a documented restriction: docs/LANGUAGE_FEATURES.md,
# Generators).
from typing import Iterator

from tpy import int32


def evens(start: int32) -> Iterator[int32]:
    try:
        i = start
        while i < start + 10:
            i += 2
            yield i
    finally:
        print("evens finally")


def main() -> None:
    g = evens(0)
    for v in g:
        print(v)
        # The subject: the name the running loop iterates is deleted.
        del g  # tpyc: error(/cannot delete 'g' inside a 'for' loop over it/)
    print("after")


main()
