from typing import Sized
from tpy import Int32

# Error: Protocol type 'Sized' cannot be used as a field type
class BadRecord:
    items: Sized

def main() -> None:
    pass

main()
