# Shared across the ext-exec and cpy-parity runs: the compiled Widget's enum
# field must behave like the plain Python source class. Reading the field yields
# the module enum singleton (identity preserved through the getset copy), the
# getset setter and a method setter both mutate the field, and the field also
# flows across the method boundary.
import enum_typed_fields as m

w = m.Widget(m.Color.RED)
print(w.color is m.Color.RED)          # True (singleton preserved through getset)
print(w.color.name, w.color.value)     # RED 1
print(isinstance(w.color, m.Color))    # True

w.color = m.Color.BLUE                  # getset setter
print(w.color is m.Color.BLUE)         # True
print(w.color.name, w.color.value)     # BLUE 3

w.set_color(m.Color.GREEN)             # a method also writes the field
print(w.color is m.Color.GREEN)        # True
print(w.get_color() is m.Color.GREEN)  # True (field crosses out via a method too)
