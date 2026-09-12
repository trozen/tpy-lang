# Optional[@dynamic protocol] as a record field is rejected: only the
# parameter position has a pointer-repr lowering; a field would need value-
# repr storage of an abstract base. Closes a pass-order gap: register_record
# validates fields before protocols register, so the check has to run
# in a second pass.
from typing import Protocol, Optional
from tpy import int32, dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Holder:
    x: Optional[Pet]  # tpyc: error(/Optional\[Pet\] is only supported at a parameter position/)

    def __init__(self) -> None:
        self.x = None
