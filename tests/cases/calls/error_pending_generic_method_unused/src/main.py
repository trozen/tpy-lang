# Error: the pending-instance guard refuses a member call with the method's
# own type parameters even where the body never uses them (accepted, filed
# with BUGS.md#pending-generic-method-one-call-path).
from __future__ import annotations


class Holder[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Duo[A, B]:
    a: list[A]

    def __init__(self) -> None:
        self.a = []

    def pin_b(self, y: B) -> None:
        pass

    def from_h[T](self, x: A, h: Holder[T]) -> None:
        self.a.append(x)
        print(len(self.a))


def run[T](h: Holder[T]) -> None:
    d = Duo()
    d.from_h(1, h)  # tpyc: error(/Cannot call generic method 'from_h' on 'd' before the type arguments of 'Duo' are known/)
    d.pin_b(7)
    print(len(d.a))


def main() -> None:
    run(Holder(5))


main()
