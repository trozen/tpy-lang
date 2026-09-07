# An assert-narrowed generator whose alias name `__a` is already a frame
# field: the collision rename the flat scope needs is not modelled.
from typing import Iterator


def gen(a: int | str) -> Iterator[str]:  # tpyc: error(/res\.narrowed_resume/)
    __a = 5
    # The narrowing alias for `a` collides with the local `__a` above.
    assert isinstance(a, int)
    yield str(a + __a)
    yield str(__a)


def main() -> None:
    for s in gen(1):
        print(s)


main()
