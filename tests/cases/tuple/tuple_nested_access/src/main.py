# Nested tuple creation and chained element access
from tpy import Int32

def main() -> None:
    n = (Int32(10), ("inner", True))
    inner = n[1]
    print(inner[0])
    print(inner[1])

    # Return value access
    print(n[0])
    print(n[1])

main()
