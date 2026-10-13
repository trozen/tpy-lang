# The call names the variant sema ranked first although a weaker one is
# declared first (winner-first twin: calls/dispatch_variant_names_protocol).
from typing import Iterable, Protocol
from tpy import String, dispatch, readonly


class Animal(Protocol):
    def name(self) -> str: ...


class Dog:
    def __init__(self, nm: str) -> None:
        self.nm = nm

    @readonly
    def name(self) -> String:
        return String(self.nm)


# --- concrete vs protocol: the concrete parameter renders `const Dog&` ---
@dispatch
def describe(x: Animal) -> str:
    return "animal " + x.name()


@dispatch
def describe(x: Dog) -> str:
    return "dog " + x.name()


def concrete_section() -> None:
    d = Dog("Rex")
    print("concrete:", describe(d))  # the Dog variant, not the template
    d.nm = "Max"
    print("concrete:", describe(d))


# --- explicit vs structural protocol: `list` declares `Iterable`, and only
# structurally has `__len__`; sema ranks the declared conformance first ---
class HasLen(Protocol):
    def __len__(self) -> int: ...


@dispatch
def count(v: HasLen) -> str:
    return "sized " + str(len(v))


@dispatch
def count(v: Iterable[int]) -> str:
    n = 0
    for _ in v:
        n += 1
    return "iterable " + str(n)


def protocol_section() -> None:
    xs = [1, 2]
    print("protocol:", count(xs))  # the declared `Iterable` variant
    xs.append(3)
    print("protocol:", count(xs), xs)


def main() -> None:
    concrete_section()
    protocol_section()


main()
