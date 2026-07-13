# Shared across ext-exec and cpy-parity: partial slot-family overrides must
# resolve the missing half through the MRO exactly like plain Python -- the
# custom base __ne__ wins over !derived_eq, the inherited __setitem__ and
# __radd__ halves keep working next to own overrides, and equality delegates
# across an intermediate ancestor with no comparisons (multi-hop).
import class_inheritance_slots as m

g1, g2 = m.Gate("ab"), m.Gate("cd")
print(g1 == g2, g1 != g2)          # False False (custom ne: equal lengths)
print(g1 + 5, 5 + g1)              # base slot, both halves: 7 52

s1, s2 = m.SubGate("ab"), m.SubGate("cd")
print(s1 == s2, s1 != s2)          # False False -- != via Gate's body, not !eq
print(s1 == m.SubGate("ab"))       # True (own __eq__)

s = m.SubGate("k")
s["a"] = 7                          # inherited __setitem__ half
print(s["a"])                      # __getitem__ (own slot inherited whole)
del s["a"]                          # own __delitem__ half
try:
    s["a"]
    print("still there")
except KeyError:
    print("deleted")

print(s + 5)                        # own __add__ -> 1005
print(5 + s)                        # inherited __radd__ -> 51

n1, n2 = m.LeafNode(3), m.LeafNode(3)
print(n1 == n2)                    # __eq__ delegated two hops (via MidNode)
print(n1 != m.LeafNode(4))         # derived-from-eq __ne__, also two hops
print(m.LeafNode(1) < m.LeafNode(2), m.LeafNode(2) < m.LeafNode(1))
print(hash(m.Node(9)) == 9)        # base keeps its own __hash__
print(hash(m.MidNode(2)) == 2)     # dunder-less subclass inherits eq+hash
h1, h2 = m.HashNode(4), m.HashNode(4)
print(hash(h1) == 104)             # own __hash__ ...
print(h1 == h2, h1 != m.HashNode(5))  # ... keeps the inherited comparisons
