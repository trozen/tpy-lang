# Four-param bound chain `W: V`, `V: U`, `U: T` with LHS-hinted T;
# pins that the inference path resolves the full chain end-to-end.
from __future__ import annotations
from tpy import Ptr, Own
from tpy.unsafe import unsafe_take, unsafe_release


class Leaf:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def kind(self) -> str:
        return "Leaf:" + self.label


class Holder[T]:
    _payload: Ptr[T]
    def __init__(self, payload: Ptr[T]) -> None:
        self._payload = payload
    def __del__(self) -> None:
        unsafe_release(self._payload)
    def get(self) -> T:
        return self._payload


def make[T, U: T, V: U, W: V](value: Own[W]) -> Own[Holder[T]]:
    return Holder[T](unsafe_take(value))


def main() -> None:
    h: Holder[Leaf] = make(Leaf("d"))  # tpyc: type(Holder[Leaf])
    print(h.get().kind())


main()
