# A walrus-bound context manager registers a temp whose pre-declaration flush
# order is unmodelled, so the `with` gate rejects it.
class Manager:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __enter__(self) -> int:
        return self.n

    def __exit__(self, t: None, v: None, tb: None) -> None:
        pass


def run() -> None:
    with (m := Manager(1)):  # tpyc: error(/expr.walrus/)
        print(m.n)


def main() -> None:
    run()


main()
