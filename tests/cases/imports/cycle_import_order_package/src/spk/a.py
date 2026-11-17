# `spk.c` is bound on the suspended `spk` once it finishes, during this
# module's run, so the class right after reads it through the package.
import spk.c


class K(spk.c.Y):  # tpyc: ok
    pass


class E:
    def __init__(self, v: int) -> None:
        self.v = v


# `sb` runs while `spk.a` is still being imported.
import sb  # noqa: E402


def main() -> None:
    e = E(1)
    sb.grow(e)
    # `grow` took the object main holds by reference.
    print("attached:", K().y, e.v)
