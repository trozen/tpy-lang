# Defaults of an import-cycle member resolve once every peer is finalized,
# in the module they are written in: a later peer's enum member, and a
# constant read from a module that depends on the cycle and names its own.
from typing import Final
from tpy import int32
from a_user import Q, f, own_call
from z_peer import peer_call

K: Final[int32] = 1000

# module level of a module depending on the cycle
print("dependent module level", f(y=9))
print("cycle peer enum", Q().add(c=9))
print("cycle peer enum trailing", Q().add_trailing(), own_call(), peer_call())
print("own", K)
