# hash() builtin on various hashable types
from tpy import Int32, UInt64, Char

def main() -> None:
    # hash() returns UInt64 and compiles for all hashable types
    h1: UInt64 = hash("hello")
    h2: UInt64 = hash(Int32(42))
    h3: UInt64 = hash(42)
    h4: UInt64 = hash(3.14)
    h5: UInt64 = hash(True)
    c: Char = "a"
    h6: UInt64 = hash(c)

    # Same value should produce the same hash
    print(hash("hello") == hash("hello"))
    print(hash(Int32(10)) == hash(Int32(10)))
    print(hash(42) == hash(42))
    print("ok")

main()
