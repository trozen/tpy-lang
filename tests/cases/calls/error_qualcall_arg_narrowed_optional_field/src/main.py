# A narrowed `Optional[list]` FIELD as a native call's argument: the
# occurrence types as a plain container but the read still unwraps the
# optional, so the bare-field argument row must stay off it.
# `heapq.heapify(self.maybe)` still rejects.
import heapq
from tpy import Int32


class Holder:
    maybe: list[Int32] | None

    def __init__(self) -> None:
        self.maybe = None

    def fix(self) -> None:
        if self.maybe is not None:
            # The argument is a narrowed optional field.
            heapq.heapify(self.maybe)  # tpyc: error(/expr\.method_call:method\.qualcall\.arg\.other\.expr\.field_access/)


def main() -> None:
    Holder().fix()


main()
