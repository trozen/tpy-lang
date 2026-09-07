# A method call whose receiver is an OPTIONAL field (`self.opt.get()`): that
# receiver needs the outer deref unwrap the value-record arm does not emit, so
# it stays rejected.
from tpy import Int32


class Inner:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def get(self) -> Int32:
        return self.n


class Holder:
    opt: Inner | None

    def __init__(self) -> None:
        self.opt = Inner(3)

    def peek(self) -> Int32:
        return self.opt.get()  # tpyc: error(/method.recv.field_optional/)


def main() -> None:
    print(Holder().peek())


main()
