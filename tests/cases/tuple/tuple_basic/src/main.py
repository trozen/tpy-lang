# Tuple creation, annotation, and printing
from tpy import Int32

def main() -> None:
    # Annotated tuple (testing annotation support)
    t: tuple[Int32, str] = (Int32(1), "hello")
    print(t)

    # Inferred tuple type
    t2 = (Int32(42), True, "world")
    print(t2)

    # Nested tuple (inferred)
    t3 = (Int32(10), ("inner", False))
    print(t3)

main()
