# A subclass mixes a class-annotated own field (`a`) with an inferred field
# (`b`), assigned in __init__ in the opposite order to declaration -- the
# struct-member and init-list order must agree (else -Wreorder under -Werror).
from tpy import int32

class Base:
    pass

class Sub(Base):
    a: int32
    def __init__(self, a: int32, b: int32):
        self.b = b
        self.a = a

def main() -> None:
    s = Sub(1, 2)
    print(s.a)
    print(s.b)

main()
