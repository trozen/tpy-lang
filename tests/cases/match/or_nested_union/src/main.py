# Parenthesized (nested) or-pattern groups on a union subject: the group is
# flattened at parse time, so the arm behaves exactly like the flat spelling.
from dataclasses import dataclass
from tpy import int32


@dataclass
class Dog:
    tags: list[str]


@dataclass
class Cat:
    tags: list[str]


@dataclass
class Bird:
    tags: list[str]


def known(a: Dog | Cat | Bird) -> str:
    match a:
        # A group with no bindings must select the same arm as the flat
        # `case Dog() | Cat() | Bird():`.
        case (Dog() | Cat()) | Bird():
            return "known"
    return "other"


def tag(a: Dog | Cat | Bird, t: str) -> int32:
    match a:
        # Binding alternatives duplicate the arm body per member; `ts` must
        # ALIAS the matched member's list, so this append is visible to the
        # caller (a silent copy would lose it).
        case (Dog(tags=ts) | Cat(tags=ts)) | Bird(tags=ts):
            ts.append(t)
            return 1
    return 0


def read(a: Dog | Cat | Bird) -> str:
    match a:
        case (Dog(tags=ts) | Cat(tags=ts)) | Bird(tags=ts):
            return ",".join(ts)
    return ""


def main() -> None:
    d: Dog | Cat | Bird = Dog(["x"])
    print(known(d))
    print(tag(d, "y"))
    print(read(d))
    c: Dog | Cat | Bird = Cat(["p"])
    print(tag(c, "q"))
    print(read(c))
    b: Dog | Cat | Bird = Bird(["m"])
    print(tag(b, "n"))
    print(read(b))


main()
