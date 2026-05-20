# Pins the root-side @dynamic filter in directly_implements_dynamic:
# a non-@dynamic protocol Mid extends @dynamic DynBase, class Impl
# implements only Mid. Impl's C++ struct has NO DynBase base (Mid is
# non-@dynamic, no C++ inheritance contribution), so passing an Impl
# where a DynBase reference is expected MUST route through Adapter.
# Regression for a unification bug that filtered on the target's
# @dynamic flag instead of the implemented-protocol root.
from typing import Protocol
from tpy import dynamic


@dynamic
class DynBase(Protocol):
    def base_method(self) -> str: ...


class Mid(DynBase, Protocol):
    def mid_method(self) -> int: ...


class Impl(Mid):
    def base_method(self) -> str:
        return "base"

    def mid_method(self) -> int:
        return 42


def use(x: DynBase) -> str:
    return x.base_method()


def main() -> None:
    obj = Impl()
    print(use(obj))


main()
