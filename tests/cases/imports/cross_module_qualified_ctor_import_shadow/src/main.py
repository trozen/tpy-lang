# A qualified construction `pa.Box(...)` must resolve to pa.Box even when the
# same-named pb.Box is bare-imported into this module -- the bare import must
# not shadow the qualified target's module identity.
import pa
import pb
from pb import Box

def main() -> None:
    a = pa.Box(10)          # qualified pa.Box (int ctor), with bare pb.Box in scope
    a.n += 5                # mutate the constructed reference object
    print(a.n)              # 15 -- proves pa.Box (int field), not pb.Box
    print(pa.Box(1).n)      # 1 -- both-qualified still resolves correctly
    print(pb.Box("q").msg)  # q
    print(Box("hi").msg)    # hi -- bare import is pb.Box

main()
