from typing import Optional
from tpy import Int32
def build(s: str, v: Int32, c: bool) -> None:
    print((s, (v, v) if c else None))
def main() -> None:
    build('hi', Int32(1), True)
main()
