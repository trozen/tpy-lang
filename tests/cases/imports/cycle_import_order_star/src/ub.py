# What TPy's star import from `ua` binds here is `ub`'s own names copied
# back, which CPython's star import does not need to bind.
from kc import K


def helper() -> int:
    return 7


# K and helper are bound above the star import, which CPython leaves in
# place; late is ub's own def below it.
from ua import *  # noqa: E402  # tpyc: ok


def late() -> int:
    return helper() + K(1).v
