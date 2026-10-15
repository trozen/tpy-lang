# A cycle member's `@overload` variant returning `Iterator[int32]` keeps its
# body in the header, so it may not take a peer's class by value.
from a import A
from b import B

def main() -> None:
    a = A()
    for v in a.__iter__(B(3)):
        print(v)

main()
