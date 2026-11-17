from . import b
import rp.c


class Item:
    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    print("relative:", b.f(), rp.c.make())
