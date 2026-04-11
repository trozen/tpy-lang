# Test that cpp_namespace auto-prefix produces correct C++ qualified names.
# Generated code should reference mypkg::Vec2 (auto-prefixed) and
# other::Thing (explicit override).
from tpy import Int32, Ptr, Own
from mypkg.types import Vec2, add_vecs, Thing

def use_vec(v: Ptr[Vec2]) -> Int32:
    return v.x

def use_add(a: Vec2, b: Vec2) -> Own[Vec2]:
    return add_vecs(a, b)

def use_thing(t: Ptr[Thing]) -> None:
    pass

def main() -> None:
    print("ok")

main()
