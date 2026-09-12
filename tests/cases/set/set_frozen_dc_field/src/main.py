# Test set/dict with frozen dataclass as record field (hash specialization ordering)
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Tag:
    name: str
    value: int32

@dataclass
class SetItem:
    name: str
    tags: set[Tag]

@dataclass
class DictItem:
    lookup: dict[Tag, str]

def main() -> None:
    # set[frozen DC] field
    si = SetItem("test", {Tag("a", 1), Tag("b", 2)})
    print(si.name)
    print(len(si.tags))
    print(Tag("a", 1) in si.tags)

    # dict[frozen DC, V] field
    di = DictItem({Tag("x", 10): "hello", Tag("y", 20): "world"})
    print(len(di.lookup))
    print(di.lookup[Tag("x", 10)])

main()
