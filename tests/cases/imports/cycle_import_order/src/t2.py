# Three-module cycle t1 -> t3 -> t2 -> t1, entered at t2: B is defined above
# t2's cycle import, and the star import binds t1 once t1 has finished.
class B:
    def __init__(self, k: int) -> None:
        self.k = k


from t1 import *  # noqa: E402  # tpyc: ok


def bump(x: A) -> None:  # tpyc: ok
    x.n += 1


def main() -> None:
    print("t2:", A(5).n, B(6).k)
