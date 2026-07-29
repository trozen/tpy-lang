# Ext-only: the argument-form error paths, plus the ONE message the glue writes
# itself. The PyArg-native rejections (too many positionals, keyword-matching a
# positional-only param, a keyword-only param passed positionally) are asserted
# on the exception TYPE only -- the C parser's wording differs from CPython's
# own (see docs/CPYTHON_INTEROP.md). The missing-required-keyword-only message
# is the exception: `$` is only legal after `|`, so PyArg cannot enforce that
# param at all and the glue raises it -- hand-written to match CPython's text
# EXACTLY, so it is asserted on str(e).
import defaults


def expect(exc, f):
    try:
        f()
    except exc:
        return "ok"
    return "NO-RAISE"


def message(f):
    try:
        f()
    except TypeError as e:
        return str(e)
    return "NO-RAISE"


# A required keyword-only param, omitted: text matches CPython verbatim.
assert message(lambda: defaults.combine(1)) == (
    "combine() missing 1 required keyword-only argument: 'b'"), \
    message(lambda: defaults.combine(1))
# ...and still reported when the OPTIONAL keyword-only sibling was supplied.
assert message(lambda: defaults.combine(1, c=2)) == (
    "combine() missing 1 required keyword-only argument: 'b'")

# A keyword-only param cannot be passed positionally (greet takes 2 positional).
assert expect(TypeError, lambda: defaults.greet("a", "b", True)) == "ok"

# A positional-only param cannot be matched by name.
assert expect(TypeError, lambda: defaults.initial(text="word")) == "ok"
assert expect(TypeError, lambda: defaults.initial("word", idx=1)) == "ok"

# Unknown keyword / duplicate argument still reject with a defaulted signature.
assert expect(TypeError, lambda: defaults.greet("a", zzz=1)) == "ok"
assert expect(TypeError, lambda: defaults.greet("a", "b", greeting="c")) == "ok"

# An omitted default still type-checks the arguments that WERE supplied.
assert expect(TypeError, lambda: defaults.advance("not-an-int")) == "ok"

# A C extension exposes no __defaults__/__kwdefaults__ and no signature -- true
# of every C-implemented callable, so host code cannot read the default values
# back out even though calling with them omitted works.
assert getattr(defaults.greet, "__defaults__", None) is None

# The same required-keyword-only report from the OTHER two emit sites: tp_init
# (which returns -1, not the wrapper's null sentinel) and a method wrapper.
# Both name the callable the way CPython does for a bound one -- QUALIFIED,
# `Pair.__init__()` rather than `Pair()` -- which is also the label the C
# parser puts in its own messages via the format string's `:name` suffix.
assert message(lambda: defaults.Pair(a=1)) == (
    "Pair.__init__() missing 1 required keyword-only argument: 'b'")
assert message(lambda: defaults.Pair()) == (
    "Pair.__init__() missing 2 required keyword-only arguments: 'a' and 'b'")
assert message(lambda: defaults.Pair(a=1, b=2).weigh()) == (
    "Pair.weigh() missing 1 required keyword-only argument: 'factor'")

# The inherited ctor's REQUIRED keyword-only marker survived the ancestor hop:
# omitting `hi` reports rather than binding it positionally. The report names
# BASE, not Derived -- CPython names an inherited callable by where it is
# defined, and the label follows the declaring class for that reason.
assert message(lambda: defaults.Derived(1)) == (
    "Base.__init__() missing 1 required keyword-only argument: 'hi'")
# ...`hi` is genuinely keyword-only, not just a second positional slot...
assert expect(TypeError, lambda: defaults.Derived(1, 9)) == "ok"
# ...and the inherited DEFAULT survived too (omitting `scale` is accepted).
assert defaults.Derived(1, hi=9).scale == 2

# 3+ missing required keyword-only params: the Oxford-comma join, verbatim.
assert message(lambda: defaults.blend()) == (
    "blend() missing 3 required keyword-only arguments: 'red', 'green', "
    "and 'blue'")
assert message(lambda: defaults.blend(green=2)) == (
    "blend() missing 2 required keyword-only arguments: 'red' and 'blue'")
