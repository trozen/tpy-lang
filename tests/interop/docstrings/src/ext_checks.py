# Ext-only: a dunder is wired as a CPython type slot, which carries no doc
# field, so its docstring cannot cross and CPython substitutes its own generic
# text. That diverges from the same source under CPython (which reports the
# real docstring), so it cannot live in driver.py -- the parity run compares
# both outputs byte-for-byte. Asserted on the compiled side only, without
# pinning CPython's exact substitute wording (it varies by version).
import docstrings

init_doc = docstrings.Counter.__init__.__doc__
assert init_doc is not None, "CPython substitutes a generic slot doc"
assert init_doc != "Start at `value`.", (
    "the source docstring must NOT have crossed: %r" % (init_doc,))

# The class docstring is the crossing place for that text, and it does cross.
assert docstrings.Counter.__doc__ == "A counter with a documented surface."

# A plain annotated field carries no docstring in Python either, so its getset
# must carry none. Pinned because the byte-diff cannot catch a wrong doc here
# (the snapshot is regenerated from whatever is emitted) and the parity driver
# cannot either -- under CPython the source's bare `value: Int64` annotation
# means Counter.value does not exist at all. A regression that emitted the
# rendered "nullptr" as TEXT would show up here as __doc__ == "nullptr".
assert docstrings.Counter.value.__doc__ is None, (
    "a plain field must have no docstring, got %r"
    % (docstrings.Counter.value.__doc__,))

# An EMPTY docstring does not survive on a function or method: CPython reads
# an empty `ml_doc` as no doc at all and reports None, where the same source
# under CPython reports ''. Its own behavior for C-level callables, not
# something the glue can change -- and nothing is lost, so it does not warn.
# (A module is the exception: an empty `m_doc` does come back as ''.)
assert docstrings.empty_doc.__doc__ is None, (
    "an empty docstring cannot survive ml_doc, got %r"
    % (docstrings.empty_doc.__doc__,))
