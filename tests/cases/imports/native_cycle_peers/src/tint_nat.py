__tpy__ = {"mod": "native", "includes": ["tint_types.hpp"]}
from tpy import int32
from tpy.extern import native


@native("tints::base")
def base() -> int32: ...
