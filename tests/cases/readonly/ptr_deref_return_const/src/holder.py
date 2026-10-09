# Methods returning what a `Ptr` field points at; `main.py` calls them on a
# readonly receiver.
from tpy import int32, Ptr


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class M:
    _a: Ptr[A]
    _b: Ptr[A]
    own: A

    def __init__(self, a: A, b: A) -> None:
        self._a = a
        self._b = b
        self.own = A(0)

    # The pointee is not self's storage, so the method is inferred const.
    def direct(self) -> A:
        return self._a  # tpyc: ok

    def pick(self, first: bool) -> A:
        return self._a if first else self._b  # tpyc: ok

    def via_local(self) -> A:
        a = self._a
        return a

    # Self's own storage keeps the method non-const.
    def mine(self) -> A:
        return self.own


class Holder[T]:
    _payload: Ptr[T]

    def __init__(self, payload: Ptr[T]) -> None:
        self._payload = payload

    def get(self) -> T:
        return self._payload  # tpyc: ok
