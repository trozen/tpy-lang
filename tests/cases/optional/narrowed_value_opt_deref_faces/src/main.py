# Narrowed value-Optional sources at reassignment/aug-assign sinks deref
# to the inner value; an Optional target keeps the whole-optional copy.
from tpy import int32


def reassign(p: int32 | None) -> int32:
    q = 0
    if p is not None:
        q = p
    return q


def reassign_view(p: str | None) -> str:
    t = "d"
    if p is not None:
        t = p
    return t


def aug_param(p: int32 | None) -> int32:
    t = 10
    if p is not None:
        t += p
    return t


def aug_loopvar(d: dict[str, int32 | None]) -> int32:
    total = 0
    for val in d.values():
        if val is not None:
            total += val
    return total


def whole_copy(p: int32 | None) -> int32:
    q2: int32 | None = None
    if p is not None:
        q2 = p
    if q2 is not None:
        return q2
    return -1


def main() -> None:
    print(reassign(5))
    print(reassign(None))
    print(reassign_view("v"))
    print(reassign_view(None))
    print(aug_param(3))
    print(aug_param(None))
    d: dict[str, int32 | None] = {"a": 1, "b": None, "c": 2}
    print(aug_loopvar(d))
    print(whole_copy(6))
    print(whole_copy(None))


main()
