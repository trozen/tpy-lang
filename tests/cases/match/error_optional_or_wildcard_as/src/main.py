# An `as` on an or-group of an Optional subject binds the INNER value, read
# through the deref -- so it is admitted only when every alternative implies
# has-value. A group that also matches None (here via the wildcard
# alternative; `case None | 1 as x:` is the other spelling) keeps rejecting:
# CPython binds the subject itself there, which the inner-typed binding
# cannot represent.
from typing import Optional

from tpy import int32


def f(o: Optional[int32]) -> int32:
    match o:  # tpyc: error(/stmt\.match/)
        case 1 | _ as x:
            return x


def main() -> None:
    print(f(None))


main()
