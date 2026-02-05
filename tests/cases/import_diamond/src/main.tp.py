from tpy import Int32
from mod_b import b_value
from mod_c import c_value

def main() -> Int32:
    print(b_value())
    print(c_value())
    return Int32(0)

main()
