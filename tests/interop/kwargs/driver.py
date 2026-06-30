# Shared across the ext-exec and cpy-parity runs: every call form below must
# behave identically for the compiled extension and the plain Python source.
# Covers all-positional, all-keyword, mixed, and reordered-keyword calls on a
# free function, a class __init__, and a method -- plus a keyword call that
# mutates through and is observed on the same object (reference semantics).
import kwargs

print(kwargs.total(1, 2, 3))                # 6   all-positional
print(kwargs.total(a=1, b=2, c=3))          # 6   all-keyword
print(kwargs.total(1, c=3, b=2))            # 6   mixed
print(kwargs.total(c=3, a=1, b=2))          # 6   reordered keyword

print(kwargs.label_of(5, "five"))           # five
print(kwargs.label_of(value=5, name="kw"))  # kw   str param by keyword
print(kwargs.label_of(7, name="seven"))     # seven

v = kwargs.Vec(x=1, y=2)                     # keyword __init__
print(v.x, v.y)                             # 1 2
v.move(dx=10, dy=20)                        # keyword method, mutate
print(v.x, v.y)                             # 11 22  -- observed on the same object
print(v.dot(other_x=2, other_y=3))          # 11*2 + 22*3 = 88

w = kwargs.Vec(3, 4)                         # positional construction still works
print(w.dot(2, other_y=1))                  # mixed on a method: 3*2 + 4*1 = 10
