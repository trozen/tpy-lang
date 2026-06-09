# Regression: a @nocopy lvalue manager in `with` must be borrowed -- a by-value
# ctx slot would copy it, which @nocopy makes a compile error (so this fails to
# build pre-fix). Runs under CPython too (@nocopy is a runtime no-op there).
from tpy import nocopy


@nocopy
class Guard:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> None:
        self.n += 1

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        self.n += 100


def run(g: Guard) -> None:
    with g:
        print("inside:", g.n)
    print("after:", g.n)


def main() -> None:
    g = Guard()
    run(g)


main()
