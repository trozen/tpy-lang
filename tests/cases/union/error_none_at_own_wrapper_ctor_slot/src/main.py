# `None` at an `Own[recursive-union]` constructor slot spells the monostate
# member, a row the Own thread has not witnessed, rejecting `Holder(None)`.
from tpy import Own


type Value = None | bool | int | str | list[Value] | dict[str, Value]


class Holder:
    value: Value

    def __init__(self, value: Own[Value]) -> None:
        self.value = value


def build() -> None:
    h = Holder(None)  # tpyc: error(/call.ctor_arg.own_union/)
    print("done")


def main() -> None:
    build()


main()
