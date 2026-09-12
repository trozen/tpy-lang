from typing import Optional
from tpy import int32
def build(s: str, v: int32, c: bool) -> None:
    print((s, (v, v) if c else None))
def main() -> None:
    build('hi', int32(1), True)
main()
