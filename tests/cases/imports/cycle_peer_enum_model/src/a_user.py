# Import-cycle member with a JSON @model whose field is the enum of z_peer,
# a peer that registers later.
from tpy import int32
from tplib.json.model import model
from z_peer import E


@model
class C:
    n: int32
    e: E  # the class macro reads the peer enum's members  # tpyc: ok

    def __init__(self, n: int32, e: E) -> None:
        self.n = n
        self.e = e


def dump(c: C) -> str:
    return c.to_json()
