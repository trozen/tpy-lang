# Shared across the ext-exec and cpy-parity runs: every call form below must
# behave identically for the compiled extension and the plain Python source.
# The load-bearing rows are the ones that SKIP a defaulted slot and pass a
# later param by keyword -- dropping trailing arguments could not express them.
import defaults

print(defaults.greet("Ann"))                          # Hello, Ann
print(defaults.greet("Ann", "Hi"))                    # Hi, Ann
print(defaults.greet("Ann", "Hi", excited=True))      # Hi, Ann!
print(defaults.greet("Ann", excited=True))            # Hello, Ann!   skips greeting
print(defaults.greet(name="Bo", greeting="Yo"))       # Yo, Bo
print(defaults.greet("Ann", greeting="Hey"))          # Hey, Ann

# A default is a constant, so repeating the call must repeat the value -- the
# per-call rematerialization must not accumulate state the way a shared mutable
# default would in CPython.
print(defaults.advance(1), defaults.advance(1), defaults.advance(1, 100))

print(defaults.scale(4.0), defaults.scale(4.0, 2.0), defaults.scale(x=3.0))
print(defaults.tag(), defaults.tag(b"abcd"), defaults.tag(data=b""))
print(defaults.offset(), defaults.offset((1, 2)), defaults.offset(at=(3, 4)))
print(defaults.paint(), defaults.paint(defaults.Color.RED))

print(defaults.combine(1, b=2))                       # 125  c defaulted
print(defaults.combine(1, b=2, c=9))                  # 129
print(defaults.combine(a=4, c=1, b=3))                # 431  reordered keyword

print(defaults.blend(red=1, green=2, blue=3))          # 10203
print(defaults.blend(blue=3, red=1, green=2))          # 10203  reordered

print(defaults.initial("word"))                       # w
print(defaults.initial("word", 2))                    # r

c = defaults.Counter()                                # both params defaulted
print(c.n, c.step)                                    # 0 1
c2 = defaults.Counter(5, step=3)
print(c2.n, c2.step)                                  # 5 3
c3 = defaults.Counter(step=7)                         # skips the defaulted n
print(c3.n, c3.step)                                  # 0 7

print(c2.bump())                                      # 5 + 1*3  = 8
print(c2.bump(2))                                     # 8 + 2*3  = 14
print(c2.bump(twice=True))                            # 14 + 3+3 = 20   skips `by`
print(c2.bump(by=10, twice=True))                     # 20 + 30+30 = 80
print(c2.n)                                           # 80 -- mutation wrote through

p = defaults.Pair(a=3, b=4)                           # required keyword-only ctor
print(p.a, p.b)                                       # 3 4
print(p.weigh(factor=2))                              # 14

d = defaults.Derived(1, hi=9)                         # inherited ctor: scale defaults
print(d.lo, d.hi, d.scale, d.span())                  # 1 9 2 16
d2 = defaults.Derived(4, hi=10, scale=3)              # inherited kwonly, supplied
print(d2.lo, d2.hi, d2.scale, d2.span())              # 4 10 3 18
