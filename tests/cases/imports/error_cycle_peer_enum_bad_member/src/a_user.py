# Import-cycle member naming a member its later peer's enum does not have.
from tpy import int32
import z_peer


class C:
    e: z_peer.E = z_peer.E.Z  # no such member: the same error as outside a cycle  # tpyc: error(/'Z' is not a member of enum 'z_peer.E'/)

    def __init__(self, n: int32) -> None:
        self.n = n
