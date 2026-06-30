# Shared across the ext-exec and cpy-parity runs: the compiled enum types must
# behave like the plain Python source enums. repr()/.name/.value are used (not
# bare str(member)) so the output is stable across CPython versions.
import enums

c = enums.Color
print(repr(c.RED), c.RED.value, c.RED.name)     # <Color.RED: 1> 1 RED
print(c.RED == 1, c.BLUE == 4)                   # True True (IntEnum)
print(c.INVALID.value, c.INVALID == -1)          # -1 True (negative member)
print(isinstance(c.RED, int))                    # True
print([m.name for m in c])                       # ['INVALID', 'RED', 'GREEN', 'BLUE']
print([m.value for m in c])                      # [-1, 1, 2, 4]
print(c(4).name, c["GREEN"].value)               # BLUE 2
print(c(-1).name, c(1) is c.RED)                 # INVALID True (singletons; neg lookup)
print(c.__name__, c.__module__, c.__qualname__)  # Color enums Color

d = enums.Direction
print(repr(d.NORTH), d.NORTH.value, d.WEST.name)  # <Direction.NORTH: 0> 0 WEST
print(d.EAST == 1, isinstance(d.SOUTH, int))      # False False (plain Enum)
print([m.name for m in d])                        # ['NORTH', 'EAST', 'SOUTH', 'WEST']
print(d(2).name, d["WEST"].value)                 # SOUTH 3
print(d.__name__, d.__module__)                   # Direction enums
