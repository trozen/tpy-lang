# Rvalue container access must stay std::string (temporary lifetime)
from tpy import int32, Own

class Pair:
    first: str
    second: str

    def __init__(self, first: str, second: str) -> None:
        self.first = first
        self.second = second

def get_tuple() -> tuple[int32, str]:
    return (int32(1), "temp")

def get_pair() -> Own[Pair]:
    return Pair("x", "y")

def main() -> None:
    b = get_tuple()[1]  # tpyc: type(str)
    print(b)

    a = get_pair().first  # tpyc: type(str)
    print(a)

main()
