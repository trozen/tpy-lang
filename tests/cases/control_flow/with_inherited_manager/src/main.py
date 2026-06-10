# Regression: sync `with` over a subclass must resolve __enter__/__exit__
# inherited from a base (MRO-aware lookup).
class BaseCM:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> "BaseCM":
        self.n += 1
        return self

    def __exit__(self, et: None, ev: None, tb: None) -> None:
        self.n += 100


class DerivedCM(BaseCM):
    pass


def main() -> None:
    cm = DerivedCM()
    with cm:
        print("inside:", cm.n)
    print("after:", cm.n)


main()
