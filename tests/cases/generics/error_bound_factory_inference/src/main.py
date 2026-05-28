from __future__ import annotations
# Inference rejects a bound violation: Rock is not a Pet, so make[U: T] with
# U=Rock, T=Pet fails.
from typing import Protocol
from tpy import dynamic, Ptr, Own
from tpy.unsafe import unsafe_take


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Rock:
    weight: int
    def __init__(self) -> None:
        self.weight = 1


class PetBox[T]:
    _payload: Ptr[T]
    def __init__(self, payload: Ptr[T]) -> None:
        self._payload = payload
    @staticmethod
    def make[U: T](value: Own[U]) -> Own[PetBox[T]]:
        return PetBox[T](unsafe_take(value))


def main() -> None:
    # Today's message is the generic inference-failure one; the precise
    # bound diagnostic is a separate TODO -- regex accepts either wording.
    b: PetBox[Pet] = PetBox.make(Rock())  # tpyc: error(/Cannot infer type arguments for 'make'|does not satisfy bound 'Pet'/)


main()
