# Function returning a tuple, accessing its elements
from tpy import Int32

def get_pair() -> tuple[Int32, str]:
    return (Int32(42), "answer")

def get_triple() -> tuple[bool, Int32, str]:
    return (True, Int32(7), "lucky")

def main() -> None:
    pair = get_pair()
    print(pair)
    print(pair[0])
    print(pair[1])

    triple = get_triple()
    print(triple)
    print(triple[0])
    print(triple[1])
    print(triple[2])

main()
