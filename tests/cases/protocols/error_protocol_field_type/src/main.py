from typing import Sized
from tpy import int32

# Error: Protocol type 'Sized' cannot be used as a field type
class BadRecord:
    items: Sized  # tpyc: error(/Protocol type.*cannot be used as a field type/)

def main() -> None:
    pass

main()
