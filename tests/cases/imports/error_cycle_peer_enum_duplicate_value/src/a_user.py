# Import-cycle member reading its later peer's enum before the peer registers.
from tpy import int32
import z_peer


class C:
    e: z_peer.E = z_peer.E.B

    def __init__(self, n: int32) -> None:
        self.n = n
