# StrView return from method call that returns owned str dangles.
from tpy import Int32, StrView

class Reader:
    _data: str
    _pos: Int32

    def __init__(self, data: str) -> None:
        self._data = data
        self._pos = 0

    def _build_str(self) -> str:
        return self._data[0:3] + "!"

    def get_view(self) -> StrView:
        return self._build_str()  # tpyc: error(/Cannot return StrView referencing a local or temporary/)

def main() -> None:
    r = Reader("hello")
    print(r.get_view())

main()
