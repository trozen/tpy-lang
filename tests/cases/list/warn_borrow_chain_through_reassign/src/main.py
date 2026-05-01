# Borrow tracker: chained alias of a vector element through a reassigned local
# is retargeted to the source container so the warning still fires. Verifies
# Layer A (registration for reassigned vars), Layer B (retarget on rebind),
# Layer C (kind promotion ALIAS->ELEMENT through a chained borrower).
from tpy import Int32


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32 = Int32(0), y: Int32 = Int32(0)) -> None:
        self.x = x
        self.y = y


def chain_alias_then_reassign() -> None:
    """`view = s; s = items[1]` retargets view to ELEMENT-borrow of items."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    s = items[Int32(0)]
    view = s
    s = items[Int32(1)]
    items.append(Point(Int32(5), Int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


def reassigned_element_borrower_warns() -> None:
    """Even without a second-level alias, a reassigned ELEMENT borrower warns."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    s = items[Int32(0)]
    s = items[Int32(1)]
    items.append(Point(Int32(5), Int32(6)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


def chain_promotes_alias_to_element() -> None:
    """ALIAS borrower of an ELEMENT borrower retargets with promoted kind."""
    items: list[Point] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    s = items[Int32(0)]
    view = s
    s = items[Int32(1)]
    # view inherited ELEMENT kind from the chain even though `view = s` was ALIAS
    items.append(Point(Int32(7), Int32(8)))  # tpyc: warning(/Mutation of 'items'.*'append'/)
    print(len(items))


chain_alias_then_reassign()
reassigned_element_borrower_warns()
chain_promotes_alias_to_element()
