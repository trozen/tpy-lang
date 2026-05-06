# Cycle peer `a` defines a class `A` whose `__init__` takes a
# cycle-peer `B` by value. Constructors are emitted inline in the
# struct (codegen `records.py:_method_can_be_out_of_line` excludes
# them), so the .hpp needs B's complete layout. The completeness
# gate must catch this even though there is no field of type B.
from a import A
from b import B

def main() -> None:
    a = A(B(42))
    print(a.val)

main()
