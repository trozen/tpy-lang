# Const across a cycle, callee in the member analyzed EARLIER (cy): the
# mirror of ca/cb with the roles reversed.
from tpy import int32
import cz


def count(xs: list[int32]) -> int32:
    return len(xs)


def back(xs: list[int32]) -> int32:
    return cz.one() + count(xs)
