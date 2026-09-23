# A module-level str the hoist sections read through a module qualifier: a
# plain variable (rebindable through `global`) and a Final constant.
from typing import Final

from tpy import StrView

PLAIN = "store-plain-module-variable-long-enough-to-defeat-sso"
CONST: Final[str] = "store-final-constant-long-enough-to-defeat-sso-xx"


def head(s: str) -> StrView:
    return s[:24]


def reset() -> None:
    global PLAIN
    PLAIN = "store-plain-REPLACED-long-enough-to-defeat-sso-yyyy"
