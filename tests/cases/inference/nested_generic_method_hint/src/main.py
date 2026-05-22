# LHS hint flows inward through an instance generic method's type param,
# then through the nested Box(...) constructor. Exercises the seed in
# `_analyze_generic_method_call` (not the static-method path that
# `nested_generic_call_hint` covers).
from typing import Protocol
from tpy import dynamic, Own
from tplib import Box, Rc


@dynamic
class Greeter(Protocol):
    def greet(self) -> str: ...


class Frog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def greet(self) -> str:
        return self.name


class Wrapper:
    def wrap[T](self, value: Own[T]) -> Own[Rc[T]]:
        return Rc.new(value)


def main() -> None:
    w = Wrapper()
    r: Rc[Box[Greeter]] = w.wrap(Box(Frog("Kermit")))  # tpyc: type(Rc[Box[Greeter]])
    print(r.get().get().greet())


main()
