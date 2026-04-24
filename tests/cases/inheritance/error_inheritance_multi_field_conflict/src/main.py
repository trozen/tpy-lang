# Field conflict across multi-base ancestry: two independent bases declaring
# the same name can't be merged in non-virtual C++ MI without ambiguous access.
from tpy import Int32


class HasId:
    id: Int32


class HasIdStr:
    id: str


class Combined(HasId, HasIdStr):  # tpyc: error(/Field 'id' declared on both 'HasId' and 'HasIdStr'/)
    pass
