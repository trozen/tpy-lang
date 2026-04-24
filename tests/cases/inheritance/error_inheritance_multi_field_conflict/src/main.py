# Multi-base same-name fields are legal in v2.2, but unqualified `self.x`
# is ambiguous when two ancestor branches each contribute the field.
# The user must disambiguate via `BaseN.x`.
from tpy import Int32


class HasId:
    id: Int32


class HasIdStr:
    id: str


class Combined(HasId, HasIdStr):
    def read_id(self) -> Int32:
        return self.id  # tpyc: error(/Ambiguous field 'id' inherited from/)
