# A @function_macro resolves a record param's field type via
# ctx.get_field_type / get_method_return_type and retypes a string-bool local.
from tpy import int32

from fieldmod import field_typed_locals


class GateBase:
    tag: int32

    def __init__(self) -> None:
        self.tag = 7


class Gate(GateBase):
    flag: bool

    def __init__(self, flag: bool) -> None:
        super().__init__()
        self.flag = flag

    def describe(self) -> str:
        return "on" if self.flag else "off"


@field_typed_locals
def check(gate: Gate) -> bool:
    armed = "true"
    return armed and gate.flag


def main() -> None:
    print(1 if check(Gate(True)) else 0)
    print(1 if check(Gate(False)) else 0)


main()
