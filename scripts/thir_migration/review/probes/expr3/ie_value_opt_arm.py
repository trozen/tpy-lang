from tpy import Int32
from typing import Optional
def main() -> None:
    c = True
    x: Optional[Int32] = (1 + 2) if c else None
    if x is not None:
        print(x)
main()
