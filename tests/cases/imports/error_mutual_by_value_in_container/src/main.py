# Cycle members reference each other through a container field
# (`list[B]`). The container stores elements by value internally,
# so this is a complete-type-required position the gate rejects.
from a import A
from tpy import Int32

def main() -> Int32:
    return 0

main()
