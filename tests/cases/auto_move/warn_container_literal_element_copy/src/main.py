# A reference-type lvalue stored as a list/set/dict literal element copies into
# the container's owned storage (storage form) and warns like .append/.insert.
from dataclasses import dataclass
from tpy import copy

class P:
    def __init__(self, v: int):
        self.v = v

@dataclass(frozen=True)
class K:  # hashable -> usable as a set element
    v: int

def main():
    p = P(1)
    a = [p]            # tpyc: warning(/copies P into owned storage/)
    b: list[P] = [p]   # tpyc: warning(/copies P into owned storage/)
    d = {"k": p}       # tpyc: warning(/copies P into owned storage/)
    e = [P(2)]         # tpyc: ok
    f = [copy(p)]      # tpyc: ok
    k = K(7)
    s = {k}            # tpyc: warning(/copies K into owned storage/)
    # p and k still live below, so the stores above are copies, not last-use moves.
    print(len(a) + len(b) + len(d) + len(e) + len(f) + len(s) + p.v + k.v)

main()
