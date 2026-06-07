# Cross-module enum used as a field default in the importing module's record
# AND in a record defined here (same-module qualification path).
from enum import Enum


class RemoteState(Enum):
    IDLE = 0
    BUSY = 1


class Remote:
    state: RemoteState = RemoteState.IDLE
