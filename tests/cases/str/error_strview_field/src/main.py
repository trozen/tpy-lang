# StrView cannot be used as a record field (dangling risk)
from tpy import StrView

class Wrapper:
    sv: StrView  # tpyc: error(/StrView cannot be used as a field/)

    def __init__(self, sv: StrView) -> None:
        self.sv = sv
