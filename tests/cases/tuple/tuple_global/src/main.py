# Tuple at module level (global scope) with type inference
from tpy import int32

t1 = (int32(1), "hello")
t2 = (int32(42), True)

def main() -> None:
    print(t1)
    print(t2)
    print(t1[0])
    print(t2[1])

main()
