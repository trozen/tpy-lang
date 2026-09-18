# Regression: iter var read AFTER the loop in an async body. Sema's
# `_promote_pending_loop_var` (triggered by the post-loop read) sets
# `for_stmt.hoist_loop_var=True` and registers a pre-declaration in
# `analyzer.if_branch_decls[id(for_stmt)]`. Before the fix, the async
# lift pre-pass (`_lift_compound_subbodies`) unconditionally cloned
# any compound stmt with sub-bodies via `copy.copy`, generating a new
# `id()` that orphaned the if_branch_decls entry. Codegen's
# `_emit_branch_decls` then found an empty dict for the cloned stmt
# id, skipped the pre-declaration, and emitted `it = *__beg_0;` to an
# undeclared `it`. The fix preserves the original stmt object when
# sub-body lifting produced no actual changes.
import asyncio
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Container:
    async def last_n(self) -> int32:
        await asyncio.sleep(0)
        # A literal-bound local proves the loop runs, which is what keeps the
        # post-loop read (and with it the pre-declaration under test) legal;
        # a field container proves nothing. The original shape (`self.items`
        # as the head) is therefore no longer expressible with a post-loop
        # read at all -- not even by seeding the name before the loop, which
        # would remove the promotion this case exists to pin.
        items = [Item(1), Item(99)]
        for it in items:
            pass
        return it.n


async def driver() -> None:
    c = Container()
    print(await c.last_n())


asyncio.run(driver())
