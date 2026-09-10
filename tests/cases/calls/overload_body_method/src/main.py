# @dispatch methods: each carries its own body, no trailing impl
from tpy import dispatch


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    @dispatch
    def greet(self, x: int) -> str:  # tpyc: ok
        return self.name + " got " + str(x) + " treats"

    @dispatch
    def greet(self, x: str) -> str:  # tpyc: ok
        return self.name + " heard '" + x + "'"


def main() -> None:
    a = Animal("Rex")
    print(a.greet(3))
    print(a.greet("hello"))


main()
