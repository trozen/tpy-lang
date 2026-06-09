# Regression: `with` on a reference-type lvalue manager must borrow it (not
# copy), so __enter__/__exit__ act on the original. Mutate-and-observe + reuse.

class Counter:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> None:
        self.n += 1

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        self.n += 100


def run(c: Counter) -> None:
    with c:
        print("inside:", c.n)
    print("after:", c.n)


def main() -> None:
    c = Counter()
    run(c)
    # Same manager reused -- counts accumulate on the one shared object.
    with c:
        print("second inside:", c.n)
    print("second after:", c.n)


main()
