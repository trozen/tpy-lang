# __exit__ runs on the context manager after the body on every path, so a
# consume of the manager inside its own with body must copy (with
# warning), not move -- __exit__ must observe the original object.
from tpy import int32, Own


class Guard:
    vals: list[int32]

    def __init__(self):
        self.vals = [1, 2]

    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit sees", len(self.vals))


class K:
    stored: list[Guard]

    def __init__(self):
        self.stored = []

    def take(self, g: Own[Guard]):
        self.stored.append(g)


def main():
    k = K()
    g = Guard()
    with g:
        k.take(g)  # tpyc: warning(/copies/)


main()
