from typing import Sized
from tpy import int32

# Error: Protocol type cannot be used as a container element type
def process(items: list[Sized]) -> None:  # tpyc: error(/Protocol type.*cannot be used as a container element/)
    pass

def main() -> None:
    pass

main()
