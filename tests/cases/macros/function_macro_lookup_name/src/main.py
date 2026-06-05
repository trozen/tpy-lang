# A @function_macro resolves module-visible names (local record, imported
# record, enum) via ctx.lookup_imported_name and retypes a string-bool local.
from enum import Enum

from lampmod import Lamp
from lookmod import lookup_typed_locals


# Introspected only at compile time (enum resolution + enum_members), never
# referenced at runtime -- so it looks unused but isn't.
class Color(Enum):
    RED = 1
    GREEN = 2


class Gate:
    flag: bool

    def __init__(self, flag: bool) -> None:
        self.flag = flag


@lookup_typed_locals
def check(gate: Gate, lamp: Lamp) -> bool:
    armed = "true"
    return armed and gate.flag and lamp.lit


def main() -> None:
    print(1 if check(Gate(True), Lamp(True)) else 0)
    print(1 if check(Gate(True), Lamp(False)) else 0)


main()
