# Shared across the ext-exec and cpy-parity runs: every __doc__ on the exposed
# surface must read identically whether the module is the compiled .so or the
# TPy source under CPython. The undocumented entries pin the None case, so a
# blanket "always emit something" regression would fail here too.
import docstrings as m

print(repr(m.__doc__))
print(repr(m.documented.__doc__))
print(repr(m.undocumented.__doc__))
# UTF-8 survives the per-byte C++ escaping.
print(repr(m.unicode_doc.__doc__))
print(repr(m.Counter.__doc__))
print(repr(m.Counter.undocumented_method.__doc__))
print(repr(m.Counter.doubled.__doc__))
print(repr(m.Rejected.__doc__))
# An enum is rebuilt via the stdlib functional API, which takes no doc, so the
# docstring is set on the constructed type; members must survive that.
print(repr(m.Level.__doc__))
print(repr(m.Bare.__doc__))
print(list(m.Level), m.Level.HIGH.name, m.Level.HIGH.value)
print(repr(m.Base.__doc__))
print(repr(m.Derived.__doc__))
# Inherited method: the docstring follows the DECLARING body through the MRO,
# so the subclass reports the base's text on both sides.
print(repr(m.Derived.described.__doc__))

# An indented docstring's raw __doc__ is pinned in ext_checks.py, not here:
# CPython dedents it at compile time since 3.13, so the source side's answer
# depends on the host interpreter. getdoc() is the version-stable view -- it
# dedents whatever it is given, so both sides agree on every interpreter.
import inspect
print(repr(inspect.getdoc(m.Counter.incr)))

# help() reads the same slots; just prove it renders without raising.
print(len(inspect.getdoc(m.Counter)) > 0)
