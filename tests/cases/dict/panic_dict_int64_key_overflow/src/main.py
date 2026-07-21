# A BigInt key beyond the DECLARED Int64 width still takes the checked
# narrow: storing at 2**70 panics (CPython accepts any int, but dict[Int64]
# declares the key domain; panic_ cases skip the cpy phase).
from tpy import Int64


def main():
    d: dict[Int64, str] = {}
    k: int = 1180591620717411303424  # 2**70
    d[k] = "boom"


main()
