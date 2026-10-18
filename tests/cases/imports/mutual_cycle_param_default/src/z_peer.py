# Cycle peer sorting after a_user, defining the enum a_user's default
# names.
import enum
import a_user
from tpy import int32


class E(enum.Enum):
    A = 1
    B = 2


# a call from the peer defining the enum
def peer_call() -> int32:
    return a_user.Q().add_trailing()
