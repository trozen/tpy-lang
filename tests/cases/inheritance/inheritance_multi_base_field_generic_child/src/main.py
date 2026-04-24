# Generic child inheriting a generic parent: Box[T].value threads T from
# the child's type parameter through the parent instantiation on the MRO,
# so codegen emits `this->Box<U>::value` from Container[U]'s method body.
from tpy import Int32


class Box[T]:
    value: T


class Label:
    value: str


class Container[U](Box[U], Label):
    def __init__(self, v: U, label: str) -> None:
        Box.value = v
        Label.value = label

    def tagged(self) -> str:
        return Label.value + "=" + str(Box.value)


def main() -> None:
    c_int = Container[Int32](Int32(7), "int")
    c_str = Container[str](" s", "str")
    print(c_int.tagged())
    print(c_str.tagged())


main()
