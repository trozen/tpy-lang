# Child re-declares a field already provided by an ancestor. The write
# goes to the child's own subobject, which is legal but usually a mistake;
# the compiler surfaces this as a warning and nudges the user toward
# BaseN.field to access the ancestor's version instead.
from tpy import int32


class Parent:
    token: int32


class Child(Parent):
    token: int32  # tpyc: warning(/shadows inherited field from 'Parent'/)

    def __init__(self, n: int32) -> None:
        self.token = n
        Parent.token = n + 1

    def as_pair(self) -> str:
        return str(self.token) + "/" + str(Parent.token)


def main() -> None:
    print(Child(5).as_pair())


main()
