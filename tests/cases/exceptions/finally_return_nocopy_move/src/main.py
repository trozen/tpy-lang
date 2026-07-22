# Returning a @nocopy local under try/finally must MOVE (deferred past the
# finally chain), not copy -- the copy was a deleted-ctor C++ build error.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    n: Int32

    def __init__(self) -> None:
        self.n = 10


def f() -> Own[Handle]:
    h = Handle()
    try:
        return h
    finally:
        h.n += 1


def f_alias() -> Own[Handle]:
    # Alias-mediated mutation: structural deferral must move (a copy would be
    # a deleted-ctor build error) and the mutation must reach the result.
    h = Handle()
    a = h
    try:
        return h
    finally:
        a.n += 1


def main() -> None:
    print(f().n)
    print(f_alias().n)


main()
