# Ptr[P] for a STATIC protocol is rejected (only @dynamic protocols
# carry a runtime vtable that makes a raw pointer dispatchable).
from typing import Protocol
from tpy import Ptr


class Named(Protocol):
    def name(self) -> str: ...


def take(p: Ptr[Named]) -> None:  # tpyc: error(/Static protocol .* cannot be used as a pointer element type/)
    pass


def main() -> None:
    pass


main()
