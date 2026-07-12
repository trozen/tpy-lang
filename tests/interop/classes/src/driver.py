# Shared across the ext-exec and cpy-parity runs: the compiled Counter type must
# behave like the plain Python source class for all of this. bump() mutating
# through a borrowed parameter and the change being visible on `c` is the
# reference-semantics proof required of a reference-type boundary test.
import classes

c = classes.Counter(10, "start")
print(c.get())              # 10
c.incr(5)
print(c.get())              # 15
print(c.value)              # 15 (getset read)
c.value = 100               # getset write
print(c.get())              # 100
print(c.label)              # start
c.label = "renamed"         # str getset write
print(c.label)              # renamed
print(c.echo("hi"))         # hi (str param + str return)

classes.bump(c, 7)          # mutate through a borrowed param
print(c.value)              # 107 -- observed on the same object

d = classes.make(3)         # factory returns a fresh instance
print(d.get(), d.value, d.label)            # 3 3 made
print(isinstance(c, classes.Counter), type(c).__name__)   # True Counter

c.__init__(99, "z")         # re-init must destroy the prior payload, not leak it
print(c.get(), c.label)     # 99 z
