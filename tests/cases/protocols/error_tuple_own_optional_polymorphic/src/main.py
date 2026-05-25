# Nesting `Own[Optional[Polymorphic]]` inside a tuple element must trigger
# the same rejection as the bare shape. Pre-fix `validate_type` had no
# `TupleType` branch and `TupleType.get_element_type()` returns None, so
# the rejection above (siblings: error_own_optional_polymorphic) silently
# bypassed any nested-in-tuple shape. The added branch in
# `tpyc/sema/type_ops.py::validate_type` recurses element-wise so the
# centralized rejection fires from a single code path.
from typing import Protocol, Optional
from tpy import dynamic, Own


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    def __init__(self) -> None:
        pass


def check(pair: tuple[int, Own[Optional[Pet]]]) -> bool:  # tpyc: error(/Own\[Optional\[Pet\]\]` is not yet supported/)
    return pair[0] == 0


def main() -> None:
    pass


main()
