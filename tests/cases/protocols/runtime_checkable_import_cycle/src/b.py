from typing import Protocol
from a import Root


class Mid(Root, Protocol):
    pass


# A cycle member with no decorated ancestor: isinstance against it warns.
class Lone(Protocol):
    def g(self) -> str: ...
