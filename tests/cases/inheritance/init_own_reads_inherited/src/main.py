# Smart-rule guard: an inherited-field assign in __init__ writes to self.X
# in the body (the base ctor owns the MIL slot). A subsequent own-field
# assign whose RHS reads `self.X` must NOT be hoisted to the MIL, because
# MIL inits run before the body and would see the default-init (empty)
# inherited slot. The chain stays alive past the inherited assign in
# general -- this test pins the demotion path for the dangerous case.
from tpy import int32


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Tagged(Animal):
    name_len: int32

    def __init__(self, name: str) -> None:
        self.name = name              # inherited; goes to body
        self.name_len = int32(len(self.name))  # own; RHS reads self.name -- must demote


def main() -> None:
    t = Tagged("Rex")
    print(t.name)
    print(t.name_len)   # expected: 3 (post-mutation), not 0 (default-init)


main()
