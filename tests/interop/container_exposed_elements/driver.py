# Shared across the ext-exec and cpy-parity runs: enum and class elements cross
# containers by value. Enum elements keep their singleton identity; class-element
# reads and copy-out returns match. (Element-mutation invisibility and copy-out
# identity are ext-only -- ext_checks.py.)
import container_exposed_elements as m

out = m.cycle([m.Color.RED, m.Color.GREEN])
print([c.name for c in out])                    # ['GREEN', 'RED']
print(out[0] is m.Color.GREEN)                  # True (singleton preserved)

print(m.total([m.Counter(1), m.Counter(2), m.Counter(3)]))   # 6
print(m.dict_total({"a": m.Counter(10), "b": m.Counter(5)}))  # 15

cs = m.counters(3)                              # returns [Counter(0), Counter(1), Counter(2)]
print([c.value for c in cs])                    # [0, 1, 2]
print(isinstance(cs[0], m.Counter))             # True
print(m.total(cs))                              # 3

# enum in Hashable positions: set element and dict key both round-trip
s = m.dedup([m.Color.RED, m.Color.GREEN, m.Color.RED])
print(sorted(c.value for c in s))               # [1, 2]
print(m.Color.RED in s)                          # True (singleton membership)
print(m.weight({m.Color.RED: 10, m.Color.BLUE: 3}, m.Color.BLUE))  # 3

# an exposed enum as a tuple element (a value-type tuple element crosses)
print(m.tag_value((m.Color.RED, 7)))             # 7
print(m.tag_value((m.Color.GREEN, 7)))           # -7
