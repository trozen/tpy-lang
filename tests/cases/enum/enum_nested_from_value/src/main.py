# Nested enum value lookup: Outer.Kind(v) resolves through EnumUtil from_value.
from enum import Enum
from tpy import int32


class Message:
    class Kind(Enum):
        TEXT = 1
        IMAGE = 2


def lookup(n: int32) -> Message.Kind:
    k = Message.Kind(n)
    return k


def main() -> None:
    k = Message.Kind(1)
    print(k)
    print(k.name)
    m = lookup(2)
    print(m == Message.Kind.IMAGE)


main()
