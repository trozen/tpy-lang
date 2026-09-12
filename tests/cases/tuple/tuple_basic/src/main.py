# Tuple creation, annotation, and printing
from tpy import int32

def main() -> None:
    # Annotated tuple (testing annotation support)
    t: tuple[int32, str] = (int32(1), "hello")
    print(t)

    # Inferred tuple type
    t2 = (int32(42), True, "world")
    print(t2)

    # Nested tuple (inferred)
    t3 = (int32(10), ("inner", False))
    print(t3)

main()
