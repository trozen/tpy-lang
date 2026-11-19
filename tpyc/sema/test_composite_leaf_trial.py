"""A composite-leaf refusal recorded inside a trial rolls back with it."""
from ..thir.testutil import _compile, _entry
from ..typesys import TypeParamRef
from .own_copy import OwnCompositeLeaf

_PROGRAM = '''
def first[T](x: T) -> T:
    return x


def main() -> None:
    print(first(1))


main()
'''


def test_trial_drops_the_refusals_it_recorded():
    _, modules = _compile(_PROGRAM)
    ctx = _entry(modules).analyzer.ctx
    before = list(ctx.own_copy_obligations)
    spans = list(ctx.own_copy_inner_spans)
    noted = len(ctx.composite_leaves_noted)
    with ctx.trial_scope():
        ctx.own_copy_obligations.append(
            OwnCompositeLeaf(TypeParamRef("T"), TypeParamRef("T"), "first"))
        ctx.composite_leaves_noted.setdefault(object(), set()).add("T")
    assert ctx.own_copy_obligations == before
    assert ctx.own_copy_inner_spans == spans
    assert len(ctx.composite_leaves_noted) == noted
