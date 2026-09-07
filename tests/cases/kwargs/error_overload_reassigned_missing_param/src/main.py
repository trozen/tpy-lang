# The SHORT stub's missing parameter is REASSIGNED in the body, so the local is
# emitted after all and the arity fold cannot stand in for it.
from typing import overload


@overload
def fmt(v: int) -> str: ...


@overload
def fmt(v: int, tag: str | None) -> str: ...


def fmt(v: int, tag: str | None = None) -> str:  # tpyc: error(/sig.overload_set.arity/)
    if tag is None:
        tag = "d"
    return tag + str(v)


def main() -> None:
    print(fmt(1))


main()
