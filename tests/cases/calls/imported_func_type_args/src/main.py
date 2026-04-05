# Imported function called with explicit type args should not be
# confused with a type instantiation (parser speculatively sets
# call_type for imported names; sema must detect it's a function).
from tpy import Int32, make_default

def main() -> None:
    a = make_default[Int32]()
    print(a)

    b: str = make_default()
    print(repr(b))

main()
