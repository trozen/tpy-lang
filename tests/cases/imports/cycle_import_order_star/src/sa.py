# `sa` binds everything it exports above its cycle import, so `sb`'s star
# import binds the same names under CPython as under TPy.
class Early:
    def __init__(self, n: int) -> None:
        self.n = n


def main() -> None:
    e = Early(1)
    sb.grow(e)
    # The object main holds: `grow` took it by reference.
    print("star:", e.n, sb.fresh().n)


import sb  # noqa: E402
