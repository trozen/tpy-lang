__tpy__ = {"includes": ["cycle_types.hpp"]}
from tpy import int32, Ptr
from tpy.extern import native
import hub

@native("hubns::Tag")
class Tag:
    k: int32

    def __init__(self) -> None: ...

class Right:
    hub: Ptr[hub.Hub]

    def __init__(self, hub: Ptr[hub.Hub]) -> None:
        self.hub = hub

    def run(self) -> int32:
        return self.hub.read_tag(Tag())
