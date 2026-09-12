# hash() builtin on various hashable types
from tpy import int32, uint64, char

def main() -> None:
    # hash() returns uint64 and compiles for all hashable types
    h1: uint64 = hash("hello")
    h2: uint64 = hash(int32(42))
    h3: uint64 = hash(42)
    h4: uint64 = hash(3.14)
    h5: uint64 = hash(True)
    c: char = "a"
    h6: uint64 = hash(c)

    # Same value should produce the same hash
    print(hash("hello") == hash("hello"))
    print(hash(int32(10)) == hash(int32(10)))
    print(hash(42) == hash(42))
    print("ok")

main()
