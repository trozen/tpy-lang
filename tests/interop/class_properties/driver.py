# Shared across the ext-exec and cpy-parity runs: computed reads, setter
# write-through, container/str/bytes/enum property values, read-only write
# and `del` rejection (AttributeError on both sides -- message text differs,
# type matches), and accessor raises crossing as the same exception type.
import class_properties as m

r = m.Rect(3, 4)
print(r.area)                  # 12  (computed each read)
print(r.width)                 # 3
r.width = 10                   # setter writes through to the same object
print(r.width, r.area)         # 10 40
print(r.label)                 # rect
print(r.dims)                  # [10, 4]  (fresh list per read on both sides)
d = r.dims
d.append(9)
print(r.dims)                  # [10, 4]  (mutating the copy is invisible)
print(r.blob)                  # b'pb'
print(r.mapping)               # {'w': 10, 'h': 4}
print(r.color is m.Color.RED)  # True    (enum property preserves the singleton)
r.color = m.Color.BLUE
print(r.color is m.Color.BLUE) # True

r.tags = [7, 8]                # non-value setter (ownership transfer)
print(r.tags)                  # [7, 8]

try:
    r.area = 99
    print("area write accepted")
except AttributeError:
    print("area write: AttributeError")

try:
    del r.width
    print("del width accepted")
except AttributeError:
    print("del width: AttributeError")

try:
    del r.area
    print("del area accepted")
except AttributeError:
    print("del area: AttributeError")

try:
    r.width = -5
    print("negative width accepted")
except ValueError as e:
    print("setter raise:", e)
print(r.width)                 # 10  (failed set left the value untouched)

r.width = 200
try:
    print(r.label)
except ValueError as e:
    print("getter raise:", e)
