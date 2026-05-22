# Markerless @dynamic protocols are allowed -- they act as phylum tags
# for the polymorphism predicate (`is_polymorphic_class_type`) without
# committing to any virtual method contract. Inheriting one activates
# class-based dispatch on `Optional[ConcreteRoot]` parameters:
#   - rvalue construction into `Optional[Root]` preserves the dynamic type
#     (codegen materializes the temp at the rvalue's actual class)
#   - `isinstance(opt, Subclass)` lowers to `dynamic_cast`
# Methods are *not* made virtual through the marker -- the protocol has
# no method contract, so subclass overrides remain non-virtual (subject to
# the standard "method hides ancestor" semantics). This matches how the
# stdlib's `Throwable` activates the BaseException tree.

from typing import Optional, Protocol
from tpy import dynamic


@dynamic
class Tagged(Protocol):  # tpyc: ok -- markerless @dynamic phylum tag
    pass


class Root(Tagged):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Sub(Root):
    extra: int

    def __init__(self, name: str, extra: int) -> None:
        super().__init__(name)
        self.extra = extra


def classify(r: Optional[Root]) -> str:
    if r is None:
        return "<none>"
    if isinstance(r, Sub):  # tpyc: ok -- dynamic_cast through Tagged-rooted chain
        narrowed = r  # tpyc: type(Sub)
        return f"sub:{narrowed.name}:{narrowed.extra}"
    return "root:" + r.name


def main() -> None:
    # rvalue construction into Optional[Root] -- preserved as Sub via no-slice
    print(classify(Sub("a", 7)))
    print(classify(Root("b")))
    print(classify(None))


main()
