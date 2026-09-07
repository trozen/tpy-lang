# An already-`Any` source carries no into-Any coerce, so the member-init row's
# explicit coercion shape does not match. Concretely, `self.payload = p`
# where `p: Any` in `Holder.__init__`; TPy rejects that member init today.
from typing import Any


class Holder:
    payload: Any

    def __init__(self, p: Any) -> None:
        self.payload = p  # tpyc: error(/ctor.mil_field.any.name/)


def main() -> None:
    h = Holder(1)
    print("built")


main()
