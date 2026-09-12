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
    items: list[Item]

    def __init__(self) -> None:
        self.items = []
        self.items.append(Item(1))
        self.items.append(Item(99))

    async def last_n(self) -> int32:
        await asyncio.sleep(0)
        for it in self.items:
            pass
        return it.n


async def driver() -> None:
    c = Container()
    print(await c.last_n())


asyncio.run(driver())
