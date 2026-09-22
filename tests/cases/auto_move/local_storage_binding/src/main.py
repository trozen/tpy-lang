# Local moves must work inside control-flow bodies and in hoisted storage.
# nocopy catches implicit copies; live aliases must still share mutations.
import asyncio
from typing import Iterator
from tpy import Own, int32, nocopy, copy, error_return, ReturnException
from tpy import copy as clone
import tpy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


class Item:
    value: int32

    def __init__(self, value: int32):
        self.value = value


class Wrapper:
    item: Item

    def __init__(self, value: int32):
        self.item = Item(value)


@nocopy
class MoveWrapper:
    item: Cell

    def __init__(self, value: int32):
        self.item = Cell(value)


class Manager:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        print("with exit")


class Failure(Exception, ReturnException):
    pass


def scoped(n: int32, stop: bool) -> int32:
    result = 0
    # A fresh owner on each iteration must survive continue and break edges.
    for i in range(n):
        source = Cell(i)
        target = source  # tpyc: ok
        target.value += 10
        if i == 1:
            continue
        result += target.value
        if stop:
            break
    return result


def branch(flag: bool) -> int32:
    source = Cell(3)
    if flag:
        # The owning destination is read after the branch that initializes it.
        target = source  # tpyc: ok
    else:
        return 0
    target.value += 10
    return target.value


def scoped_branch(flag: bool) -> int32:
    if flag:
        source = Cell(3)
        # A branch-local owner need not acquire function-level storage.
        target = source  # tpyc: ok
        return target.value
    return 0


def hoisted_chain(flag: bool) -> int32:
    first = Cell(4)
    if flag:
        source = first
    else:
        return 0
    if flag:
        # The move reads the stored Cell, not its optional backing wrapper.
        target = source  # tpyc: ok
        target.value += 10
        return target.value
    return 0


def closure_collision() -> int32:
    source = Cell(1)

    def nested(borrow: Cell, flag: bool) -> int32:
        if flag:
            # The outer target's consume decision must not move this borrow.
            target = borrow  # tpyc: ok
            target.value = 9
            return target.value
        return 0

    target = source
    other = Cell(2)
    nested(other, True)
    print("closure alias", other.value)
    return target.value


def hoisted_loops() -> int32:
    for i in range(3):
        source = Cell(i)
        # The final iteration's owner remains available after the loop.
        target = source  # tpyc: ok
    result = target.value
    count = 3
    while True:
        original = Cell(count)
        # While and range-for must select the same hoisted storage operation.
        last = original  # tpyc: ok
        count -= 1
        if count == 0:
            break
    return result + last.value


def native_loop(items: list[int32]) -> int32:
    result = 0
    for item in items:
        source = Cell(item)
        # Native iteration has its own body adapter, but the same move rule.
        target = source  # tpyc: ok
        result += target.value
    return result


def managed() -> int32:
    with Manager() as value:
        source = Cell(value)
        # The destination outlives the with body without copying its owner.
        target = source  # tpyc: ok
        target.value += 10
    return target.value


def finalized() -> int32:
    try:
        source = Cell(5)
        # A return through finally must retain the selected owning storage.
        target = source  # tpyc: ok
        return target.value
    finally:
        print("finally exit")


def matched(tag: int32) -> int32:
    source = Cell(6)
    match tag:
        case 1:
            # Ordinary arm-local assignments use the common hoist decision.
            target = source  # tpyc: ok
        case _:
            return 0
    return target.value


@error_return(Failure)
def propagated(n: int32) -> int32:
    result = 0
    for i in range(n):
        source = Cell(i)
        # Error-return bodies must admit the same local move as plain bodies.
        target = source  # tpyc: ok
        result += target.value
    return result


class Runner:
    value: int32

    def __init__(self, n: int32):
        self.value = 0
        for i in range(n):
            source = Cell(i)
            # Constructor-tail locals own independently of receiver fields.
            target = source  # tpyc: ok
            self.value += target.value

    def method(self, n: int32) -> int32:
        for i in range(n):
            source = Cell(i)
            # Receiver methods share the free-function storage decision.
            target = source  # tpyc: ok
            self.value += target.value
        return self.value

    @staticmethod
    def static(n: int32) -> int32:
        result = 0
        for i in range(n):
            source = Cell(i)
            # Static-method locals obey the same ownership rule as free functions.
            target = source  # tpyc: ok
            result += target.value
        return result


def containers() -> None:
    for i in range(2):
        values = [Cell(i)]
        values.append(Cell(3))
        # Append keeps this dynamic; the unannotated fixed list below is Array.
        moved = values  # tpyc: ok
        moved[0].value = 9
        fixed = [Cell(i)]
        array = fixed  # tpyc: ok
        array[0].value = 8
        mapping = {1: Cell(i)}
        table = mapping  # tpyc: ok
        table[1].value = 7
        unique = {i}
        members = unique  # tpyc: ok
        members.add(6)
        buffer = bytearray(b"ab")
        data = buffer  # tpyc: ok
        data[0] = 99
        print("containers", moved[0].value, array[0].value, table[1].value, len(members), data[0])


def hoisted_containers(flag: bool) -> None:
    source = [Cell(1)]
    source.append(Cell(2))
    if flag:
        # Non-copyable elements catch a copy into the hoisted container home.
        target = source  # tpyc: ok
    else:
        return
    target[0].value = 9
    mapping = {1: Cell(3)}
    match flag:
        case True:
            # Match must use the same owner write as the if adapter.
            table = mapping  # tpyc: ok
        case _:
            return
    table[1].value = 8
    print("hoisted containers", target[0].value, table[1].value)


def aliases() -> None:
    for i in range(2):
        source = Cell(i)
        # A later source read forbids consuming it at the binding.
        target = source  # tpyc: ok
        target.value = 7
        print("live source", source.value)
        original = Cell(i)
        retained = original
        # A surviving third alias must see writes through the new binding.
        other = original  # tpyc: ok
        other.value = 8
        print("third alias", retained.value)


def copies(flag: bool) -> None:
    items = [Item(1), Item(2)]
    for i in range(2):
        # Each spelling copies an indexed source, leaving its field unchanged.
        direct = copy(items[i])  # tpyc: ok
        renamed = clone(items[i])  # tpyc: ok
        qualified = tpy.copy(items[i])  # tpyc: ok
        direct.value = 7
        renamed.value = 8
        qualified.value = 9
        print("copies", items[i].value, direct.value, renamed.value, qualified.value)
    if flag:
        # The copied owner is hoisted, but must remain independent of its source.
        hoisted = copy(items[0])  # tpyc: ok
    else:
        return
    hoisted.value = 10
    print("hoisted copy", items[0].value, hoisted.value)


def escape_copy() -> None:
    items = [Wrapper(1), Wrapper(2)]
    holder = Item(0)
    for i in range(2):
        # Borrowing its field beyond the loop forces a backing slot for the copy.
        duplicate = copy(items[i])  # tpyc: ok
        holder = duplicate.item  # tpyc: warning(/will not keep the object/)
    holder.value = 9
    print("escape copy", items[1].item.value, holder.value)


def position_aliases(flag: bool) -> None:
    source = Cell(1)
    retained = source
    if flag:
        # A live third alias forbids a move into the branch's hoisted binding.
        target = source  # tpyc: ok
    else:
        return
    target.value = 11
    print("branch alias", retained.value)
    with_source = Cell(11)
    with Manager() as value:
        # The owner stays live after the context-manager body.
        managed_alias = with_source  # tpyc: ok
        managed_alias.value += value
    print("with alias", with_source.value)
    try_source = Cell(12)
    try:
        # A finally block observes writes through the try body's alias.
        finalized_alias = try_source  # tpyc: ok
        finalized_alias.value += 1
    finally:
        print("finally alias", try_source.value)
    match_source = Cell(13)
    # Predeclaring avoids BUGS.md#match-nonvalue-hoist-unadmitted.
    matched_alias = match_source
    match flag:
        case True:
            # The source read after match must observe writes through this alias.
            matched_alias = match_source  # tpyc: ok
            matched_alias.value += 1
        case _:
            return
    print("match alias", match_source.value)
    loop_source = Cell(14)
    for i in range(2):
        # Repeated hoisted alias writes must not move the surviving source.
        loop_alias = loop_source  # tpyc: ok
        loop_alias.value += i
    print("loop alias", loop_source.value, loop_alias.value)


def closure_controls() -> None:
    source = Cell(3)

    def read_source() -> int32:
        return source.value

    for i in range(1):
        # The later closure call keeps its captured source alive at the bind.
        target = source  # tpyc: ok
        target.value = 19
    print("live closure", read_source())

    def own_local() -> int32:
        inner = Cell(5)
        # A nested body's own last-use binding must not copy a nocopy record.
        target = inner  # tpyc: ok
        target.value += 1
        return target.value

    print("nested owner", own_local())


def frame_aliases(n: int32) -> Iterator[int32]:
    for i in range(n):
        source = Cell(i)
        # Both names cross a suspension; writes through one reach the other.
        target = source  # tpyc: ok
        yield target.value
        target.value += 10
        yield source.value
        original = Cell(i)
        # Last-use eligibility still leaves storage selection to the frame.
        moved = original  # tpyc: ok
        yield moved.value
        moved.value += 20
        yield moved.value


async def resumed_value(value: int32) -> int32:
    return value


async def async_aliases() -> None:
    for i in range(2):
        source = Cell(i)
        # An await must preserve a frame-held alias rather than move its owner.
        target = source  # tpyc: ok
        change = await resumed_value(10)
        target.value += change
        print("async alias", source.value)
        original = Cell(i)
        # The last-use source backs the frame alias across the await as well.
        moved = original  # tpyc: ok
        change = await resumed_value(20)
        moved.value += change
        print("async last use", moved.value)


def take_cell(value: Own[Cell]) -> int32:
    value.value += 1
    return value.value


class StoredCell:
    value: Cell

    def __init__(self, value: Own[Cell]):
        self.value = value


def downstream_moves() -> None:
    items: list[Cell] = []
    for i in range(2):
        source = Cell(i)
        # A newly owned local can transfer onward into an Own parameter.
        target = source  # tpyc: ok
        print("owned argument", take_cell(target))  # tpyc: ok
        element = Cell(i)
        moved_element = element  # tpyc: ok
        # Inserting the moved local must transfer its non-copyable payload.
        items.append(moved_element)  # tpyc: ok
        field = Cell(i)
        moved_field = field  # tpyc: ok
        # Constructor field storage consumes the moved local through Own.
        stored = StoredCell(moved_field)  # tpyc: ok
        stored.value.value += 10
        print("owned field", stored.value.value)
    items[0].value = 20
    print("owned container", items[0].value, items[1].value)


def caught_alias() -> None:
    source = Cell(1)
    try:
        # The exception handler must still observe the source's shared object.
        target = source  # tpyc: ok
        target.value = 21
        raise ValueError("test")
    except ValueError:
        print("caught alias", source.value)


def escape_move() -> None:
    holder = Cell(0)
    for i in range(2):
        source = MoveWrapper(i)
        # A field borrowed beyond the loop keeps the moved owner's slot alive.
        duplicate = source  # tpyc: ok
        holder = duplicate.item  # tpyc: warning(/will not keep the object/)
    holder.value = 22
    print("escape move", holder.value)


def binding_controls() -> None:
    position_aliases(False)
    position_aliases(True)
    closure_controls()
    for value in frame_aliases(2):
        print("generator alias", value)
    asyncio.run(async_aliases())
    downstream_moves()
    caught_alias()
    escape_move()


def main() -> None:
    print("scoped", scoped(0, False), scoped(3, False), scoped(3, True))
    print("branch", branch(False), branch(True), scoped_branch(False), scoped_branch(True))
    print("chain", hoisted_chain(False), hoisted_chain(True))
    closure_value = closure_collision()
    print("closure owner", closure_value)
    print("hoisted loops", hoisted_loops())
    print("native", native_loop([2, 3]))
    managed_value = managed()
    print("with", managed_value)
    finalized_value = finalized()
    print("finally", finalized_value)
    print("match", matched(0), matched(1))
    try:
        print("error return", propagated(3))
    except Failure:
        print("unexpected error")
    runner = Runner(3)
    print("constructor", runner.value)
    print("method", runner.method(3))
    print("static", Runner.static(3))
    containers()
    hoisted_containers(False)
    hoisted_containers(True)
    aliases()
    copies(False)
    copies(True)
    escape_copy()
    binding_controls()


main()
