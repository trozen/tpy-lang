# Regression: assigning an `Optional[Record]` field through readonly
# self into a local should preserve the Optional. Before the fix,
# `update_after_write` treated the readonly-wrapped Optional RHS as
# non-Optional and narrowed the local to the inner type at the point
# of binding, breaking the subsequent `is None` check.
from typing import Optional


class Inner:
    value: str
    def __init__(self, value: str) -> None:
        self.value = value


class Holder:
    _inner: Optional[Inner]
    def __init__(self, inner: Optional[Inner]) -> None:
        self._inner = inner

    @property
    def value(self) -> Optional[str]:
        sub = self._inner
        if sub is None:
            return None
        return sub.value


def main() -> None:
    h = Holder(Inner("hi"))
    v1 = h.value
    if v1 is not None:
        print(v1)
    h2 = Holder(None)
    print(h2.value is None)


main()
