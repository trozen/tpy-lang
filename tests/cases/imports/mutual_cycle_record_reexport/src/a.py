from b import BType
from tpy import Own

class AType:
    def make_b(self) -> Own[BType]:
        return BType()
