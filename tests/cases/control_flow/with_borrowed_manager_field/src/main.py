# Regression: a field-access manager (`with self.mgr:`) and the `as`-binding
# must borrow the original; __exit__ must fire on the shared object on the
# exception path too. Mutate-and-observe through both the field and `as`.


class Mgr:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> "Mgr":
        self.n += 1
        return self

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        self.n += 100


class Owner:
    mgr: Mgr

    def __init__(self) -> None:
        self.mgr = Mgr()

    def work(self) -> None:
        # Field-access manager + as-binding that aliases the same object.
        with self.mgr as bound:
            bound.n += 1000
            print("inside:", self.mgr.n)
        print("after:", self.mgr.n)

    def work_raises(self) -> None:
        try:
            with self.mgr:
                raise ValueError("boom")
        except ValueError:
            print("caught, n:", self.mgr.n)


def main() -> None:
    o = Owner()
    o.work()
    o.work_raises()


main()
