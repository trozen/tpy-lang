# The return expression must be evaluated before __exit__ runs: returning
# state that __exit__ mutates yields the pre-exit value, like CPython.
class Lock:
    held: bool

    def __init__(self) -> None:
        self.held = False

    def __enter__(self) -> None:
        self.held = True

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.held = False


def f(lk: Lock) -> bool:
    with lk:
        return lk.held


def main() -> None:
    lk = Lock()
    print(f(lk))
    print(lk.held)


main()
