# StrView += is disallowed because the temporary result would dangle
from tpy import StrView

def test() -> None:
    sv: StrView = StrView("hello")
    sv += " world"  # tpyc: error(/Augmented assignment target must be a numeric or string type/)

test()
