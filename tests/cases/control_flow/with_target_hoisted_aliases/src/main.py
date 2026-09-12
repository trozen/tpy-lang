# A with target hoisted to function scope by an enclosing branch-decl pass -- the
# inner target of a nested `with` where every path returns -- must still alias its
# manager. __exit__ runs against the manager, so a mutation through the target has
# to be visible there; owning hoist storage would copy and show the old value.
# Not a resumable function: this is the non-frame half of the same question.
from tpy import int32


class Logger:
    def __init__(self, tag: int32):
        self.tag = tag

    def __enter__(self) -> "Logger":
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("exit sees tag:", self.tag)


def run(flag: bool) -> int32:
    with Logger(1) as outer:
        with Logger(10) as inner:
            inner.tag = 99
            if flag:
                return outer.tag
            return 0


def main() -> None:
    # Bound first rather than nested in the call: `print(a, run())` interleaves
    # the callee's own output ahead of `a` (tracked separately in BUGS.md).
    r = run(True)
    print("returned:", r)


main()
