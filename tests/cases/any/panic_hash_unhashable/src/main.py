# hash() on an Any holding a non-Hashable value (a list) panics at
# runtime via the AnyOps null-hash-slot path. Pins the panic message
# format -- regression guard for the type_name registry.

from typing import Any
from tpy import int32


def main() -> None:
    x: Any = [int32(1), int32(2), int32(3)]
    print(hash(x))


main()
