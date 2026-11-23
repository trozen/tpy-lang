# A constructor storing a `Send[T] | None` parameter into its field copies a
# class payload (warned), as the bare `T | None` and the concrete twin do.
from tpy import Send, int32


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"C({self.n})"


class SendHolder[T]:
    _v: Send[T] | None

    def __init__(self, v: Send[T] | None) -> None:
        # The subject: the field holds a copy at a class `T`.
        self._v = v  # tpyc: warning(/may copy Send\[T\] \| None into field/)

    def show(self) -> None:
        print("send_store", self._v)


class CBox:
    _v: C | None

    def __init__(self, v: C | None) -> None:
        self._v = v  # tpyc: warning(/copies C \| None into field/)

    def show(self) -> None:
        print("twin_store", self._v)


def main() -> None:
    c = C(3)
    h = SendHolder(c)
    t = CBox(c)
    # The mutation after the store does not reach either copy.
    c.n = 30
    h.show()
    t.show()
    print("source", c)


main()
