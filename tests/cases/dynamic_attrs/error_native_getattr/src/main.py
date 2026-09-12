# D16: @native records cannot declare __getattr__ -- native record shape is
# owned by hand-written C++; mixing dyn-attr is out of scope.
from tpy.extern import native
from tpy import int32
from typing import Any

@native("MyStruct", binding="C")
class Bad:
    x: int32

    def __getattr__(self, name: str) -> Any: ...  # tpyc: error(/cannot be declared on @native/)

def main() -> None:
    pass

main()
