# Three-param bound chain `V: U`, `U: T` exercises the fixpoint iteration
# in infer_type_params_for_function: the default-T-from-U direction has to
# resolve U from V's inferred value (iter 1) before it can resolve T from
# U (iter 2). Hint-free call: only the arg has type evidence.
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
    # Body coerces Ptr[V] -> Ptr[T] transitively across U; the bound chain
    # is live during sema body analysis.
    return Holder[T](unsafe_take(value))


def main() -> None:
    # Hint-free: T not pinned by LHS. arg pins V=Leaf; fixpoint iter 1
    # defaults U=V=Leaf, iter 2 defaults T=U=Leaf. Final Holder[Leaf].
    h = make(Leaf("a"))  # tpyc: type(Holder[Leaf])
    print(h.get().kind())


main()
