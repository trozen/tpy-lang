# pkg.a binds Item before it starts pkg.b, but pkg gets its attribute `a`
# only once pkg.a finishes.
class Item:
    def __init__(self, v: int) -> None:
        self.v = v


import pkg.b  # noqa: E402


def main() -> None:
    it = Item(1)
    pkg.b.grow(it)
    print("submodule:", it.v)
