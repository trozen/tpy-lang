# PEP 484 forward-ref string annotations: `-> "ClassName"` and param "ClassName"
# resolve after the class is defined later in the same module.
from tpy import int32, Own


def make() -> Own["Container"]:
    return Container(int32(42))


def show(c: "Container") -> None:
    print(c.value)


class Container:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def clone(self) -> Own["Container"]:
        return Container(self.value)


def main() -> None:
    c = make()
    show(c)
    c2 = c.clone()
    show(c2)


main()
