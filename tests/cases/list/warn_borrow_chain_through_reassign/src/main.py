# Borrow tracker: chained alias of a vector element through a reassigned local
# is retargeted to the source container so the warning still fires. Verifies
# Layer A (registration for reassigned vars), Layer B (retarget on rebind),
# Layer C (kind promotion ALIAS->ELEMENT through a chained borrower).
from tpy import int32


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32 = int32(0), y: int32 = int32(0)) -> None:
        self.x = x
        self.y = y


def chain_alias_then_reassign() -> None:
    """`view = s; s = items[1]` retargets view to ELEMENT-borrow of items."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    s = items[int32(0)]
    view = s
    s = items[int32(1)]
    # Mutate THROUGH the rebound alias: 99 proves `s` aliases items[1]; a
    # silent copy would leave the element at 3. Before the append, while the
    # borrow is still live.
    s.x = 99
    print(items[int32(1)].x)
    items.append(Point(int32(5), int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


def reassigned_element_borrower_warns() -> None:
    """Even without a second-level alias, a reassigned ELEMENT borrower warns."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    s = items[int32(0)]
    s = items[int32(1)]
    s.x = 88
    print(items[int32(1)].x)
    items.append(Point(int32(5), int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


def chain_promotes_alias_to_element() -> None:
    """ALIAS borrower of an ELEMENT borrower retargets with promoted kind."""
    items: list[Point] = [Point(int32(1), int32(2)), Point(int32(3), int32(4))]
    s = items[int32(0)]
    view = s
    s = items[int32(1)]
    # `view` was taken while `s` aliased items[0], so it still aliases items[0]
    # after `s` was rebound away: 77 proves the chain aliased rather than copied.
    view.y = 77
    print(items[int32(0)].y)
    # view inherited ELEMENT kind from the chain even though `view = s` was ALIAS
    items.append(Point(int32(7), int32(8)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


chain_alias_then_reassign()
reassigned_element_borrower_warns()
chain_promotes_alias_to_element()
