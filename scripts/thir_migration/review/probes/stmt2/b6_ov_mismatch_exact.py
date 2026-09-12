from typing import overload, Literal
from tpy import int32
@overload
def pick2(x: Literal['a']) -> int32: ...
@overload
def pick2(x: str) -> int32 | str: ...
def pick2(x: str) -> int32 | str:
    if len(x) > 0:
        return 42
    return 'hello'
def main() -> None:
    print(pick2('a'))
main()
