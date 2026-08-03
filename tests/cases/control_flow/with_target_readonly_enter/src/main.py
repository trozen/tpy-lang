# A `@readonly __enter__` returns `const T&`, so a branch-hoisted target must
# take the CONST pointer form. Getting that verdict from the declared return type
# alone misses it -- readonly is a property of the method, not of the annotation --
# and a non-const slot against a const-returning __enter__ fails the C++ build.
# The target is first declared inside branches and read after, which is what
# forces the hoist. Mutating the MANAGER and reading through the target proves
# the hoisted slot aliases rather than copies (the target itself is const).
from tpy import Int32, readonly


class Reg:
    def __init__(self, n: Int32):
        self.n = n

    @readonly
    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def probe(flag: bool) -> Int32:
    r = Reg(1)
    if flag:
        with r as view:
            pass
    else:
        with r as view:
            pass
    r.n = 42
    return view.n


def main() -> None:
    print(probe(True))
    print(probe(False))


main()
