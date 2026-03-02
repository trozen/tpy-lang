# Tuple at module level (global scope) with type inference
from tpy import Int32

t1 = (Int32(1), "hello")
t2 = (Int32(42), True)

def main() -> None:
    print(t1)
    print(t2)
    print(t1[0])
    print(t2[1])

main()
