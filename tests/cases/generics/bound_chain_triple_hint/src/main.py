# Three-param bound chain with LHS hint: exercises the single-pass
# `seed_subst_from_return_hint` bound-following loop. The hint pins T;
# the loop must default U=T (one hop) and V=U=T (second hop). Today's
# loop iterates the param-name order once -- if T comes before U which
# comes before V in `func.type_params`, the chain resolves correctly
# in one pass. A regression that flips iteration order or breaks the
# second-hop default would surface here.
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


def make[T, U: T, V: U](value: Own[V]) -> Own[Holder[T]]:
    return Holder[T](unsafe_take(value))


def main() -> None:
    # LHS hint pins T=Leaf; seed-subst chain defaults U=T, V=U through
    # the bound chain BEFORE arg analysis sees the value. Arg type
    # confirms V=Leaf (no contradiction). Result Holder[Leaf].
    h: Holder[Leaf] = make(Leaf("a"))  # tpyc: type(Holder[Leaf])
    print(h.get().kind())


main()
