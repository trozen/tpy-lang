# Const inference when self is passed as argument to free functions.
# Methods that pass self to non-readonly free functions must not be const.
from __future__ import annotations
from tpy import readonly, Int32


class Module:
    _name: str
    _items: list[Int32]

    def __init__(self, name: str) -> None:
        self._name = name
        self._items = []

    def log(self, s: str):
        print(self._name + ": " + s)

    # Passes self to mutating free function -- must NOT be const
    def init(self):
        on_init(self)

    # Passes self to readonly free function -- SHOULD be const
    def describe(self) -> str:
        return get_description(self)

    # Passes self.field to a free function -- should stay const
    # (field access, not self mutation)
    def name_upper(self) -> str:
        return to_upper(self._name)

    # Calls a method that itself passes self to a mutating free function
    # -- transitively not const
    def reinit(self):
        self.init()

    # Passes self to a free function that structurally mutates a field
    # -- must NOT be const
    def add_item(self, val: Int32):
        append_item(self, val)


def on_init(mod: Module):
    mod.log("initialized")


@readonly
def get_description(mod: Module) -> str:
    return "Module(" + mod._name + ")"


def to_upper(s: str) -> str:
    return s


def append_item(mod: Module, val: Int32):
    mod._items.append(val)


def main() -> None:
    m = Module("test")
    m.init()
    print(m.describe())
    print(m.name_upper())
    m.reinit()
    m.add_item(42)
    print(m._items)


main()
