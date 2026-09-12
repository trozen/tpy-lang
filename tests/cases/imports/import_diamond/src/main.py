from tpy import int32
from mod_b import b_value
from mod_c import c_value

def main() -> int32:
    print(b_value())
    print(c_value())
    return int32(0)

main()
