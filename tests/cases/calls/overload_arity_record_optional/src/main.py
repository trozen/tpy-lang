# Arity-variant stubs where the missing impl param has a record-typed Optional
# default. Exercises the pointer-repr local registration (pointer_locals).
from typing import overload


class Tag:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


@overload
def fmt(value: int) -> str: ...  # tpyc: ok

@overload
def fmt(value: int, tag: Tag) -> str: ...  # tpyc: ok

def fmt(value: int, tag: Tag | None = None) -> str:
    if tag is None:
        return str(value)
    return tag.name + "=" + str(value)


def main() -> None:
    print(fmt(42))
    print(fmt(42, Tag("temp")))


main()
