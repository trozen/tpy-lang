# A lending def at a generic Fn slot whose return is Own[T] is refused at
# every instantiation, as at the concrete Fn[[P], Own[P]] twin: Own[T] is a
# decided form, not an open return.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the `Callable` -> `Fn`
# fit ("At a slot that holds its own result -- an Own[P] return ...").
from tpy import Fn, int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(p: P) -> P:
    return p


def fill[T](f: Fn[[T], Own[T]], x: T, out: list[T]) -> None:
    out.append(f(x))


def main() -> None:
    p = P(1)
    out: list[P] = []
    # The subject: ident lends, the slot holds its own result.
    fill(ident, p, out)  # tpyc: error(/'ident' cannot be passed as Fn\[\[P\], Own\[P\]\]: it returns a borrow of P/)
    print(p.n, out[0].n)


main()
