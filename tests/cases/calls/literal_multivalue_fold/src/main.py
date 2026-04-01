# Multi-value Literal dead branch elimination: or-chains, in-operator,
# and-contradiction, and not-wrapping fold to constants in specializations
from typing import Literal, overload


@overload
def handle(mode: Literal["r", "w"]) -> str: ...
@overload
def handle(mode: Literal["rb", "wb"]) -> str: ...
def handle(mode: str) -> str:
    # or-chain: covers full set for Literal["r","w"], folds to true
    # For Literal["rb","wb"], each == is false, chain folds to false
    if mode == "r" or mode == "w":
        return "text"
    return "binary"


@overload
def handle_in(mode: Literal["r", "w"]) -> str: ...
@overload
def handle_in(mode: Literal["rb", "wb"]) -> str: ...
def handle_in(mode: str) -> str:
    # in-operator with set literal: covers full set for Literal["r","w"]
    if mode in {"r", "w"}:
        return "text"
    return "binary"


@overload
def handle_not_in(mode: Literal["r", "w"]) -> str: ...
@overload
def handle_not_in(mode: Literal["rb", "wb"]) -> str: ...
def handle_not_in(mode: str) -> str:
    # not-in: disjoint for Literal["r","w"] -> false, so else taken
    if mode not in {"r", "w"}:
        return "binary"
    return "text"


@overload
def handle_and(mode: Literal["r"]) -> str: ...
@overload
def handle_and(mode: Literal["w"]) -> str: ...
def handle_and(mode: str) -> str:
    # and-contradiction: mode can't be both "r" and "w"
    if mode == "r" and mode == "w":
        return "impossible"
    if mode == "r":
        return "read"
    return "write"


@overload
def handle_and_multi(mode: Literal["r", "w"]) -> str: ...
@overload
def handle_and_multi(mode: Literal["rb", "wb"]) -> str: ...
def handle_and_multi(mode: str) -> str:
    # and-contradiction on multi-value: mode can't be "r" AND "w" at once
    if mode == "r" and mode == "w":
        return "impossible"
    return "ok"


@overload
def handle_not_in_disjoint(mode: Literal["r", "w"]) -> str: ...
@overload
def handle_not_in_disjoint(mode: Literal["rb", "wb"]) -> str: ...
def handle_not_in_disjoint(mode: str) -> str:
    # not-in with disjoint set: folds to true for Literal["r","w"]
    if mode not in {"x", "y"}:
        return "not-xy"
    return "xy"


@overload
def handle_not(mode: Literal["r", "w"]) -> str: ...
@overload
def handle_not(mode: Literal["rb", "wb"]) -> str: ...
def handle_not(mode: str) -> str:
    # not wrapping: not (or-chain covering full set) -> false
    if not (mode == "r" or mode == "w"):
        return "binary"
    return "text"


def main() -> None:
    print(handle("r"))
    print(handle("rb"))
    print(handle_in("w"))
    print(handle_in("wb"))
    print(handle_not_in("r"))
    print(handle_not_in("rb"))
    print(handle_and("r"))
    print(handle_and("w"))
    print(handle_not("w"))
    print(handle_not("rb"))
    print(handle_and_multi("r"))
    print(handle_and_multi("rb"))
    print(handle_not_in_disjoint("r"))
    print(handle_not_in_disjoint("rb"))


main()
