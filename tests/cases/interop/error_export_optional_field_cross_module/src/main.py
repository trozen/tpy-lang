# The None gate does not open a hole in the cross-module rule: a foreign
# exposed enum (or value class) behind Optional at a getset field is the
# same located error its bare sibling gets, not a glue that references a
# handle this module never defines.
# tpy: ext_module
from typing import Optional
from tpy.extern import export
from foo_mod import Hue


@export
class Holder:
    tint: Optional[Hue]  # tpyc: error(/field 'tint' is an exposed enum from another module/)

    def __init__(self) -> None:
        self.tint = None
