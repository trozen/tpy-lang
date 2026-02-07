from typing import Sized
from tpy import Int32

# Error: Protocol type cannot be used as a container element type
def process(items: list[Sized]) -> None:
    pass

def main() -> None:
    pass

main()
