# B115 regression: the record_to_ptr coercion respects cross-module identity.
# A red.Tag lvalue must still coerce to Ptr[red.Tag] when a same-short-named
# blue.Tag is in scope (the coercion compares qnames, not short names). Mutates
# through the pointer and observes, so a broken coercion (wrong record / a copy)
# would change the output.
from tpy import Ptr
from red import Tag
from blue import Tag as BlueTag


def bump(p: Ptr[Tag]) -> None:
    p.n += 1


def main() -> None:
    t = Tag(5)
    bump(t)
    print(t.n)
    b = BlueTag(10)
    print(b.n)


main()
