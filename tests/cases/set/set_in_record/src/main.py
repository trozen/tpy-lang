# Set as a field in a dataclass
from tpy import Int32
from dataclasses import dataclass

@dataclass
class TaggedItem:
    name: str
    tags: set[str]

def main() -> None:
    item = TaggedItem("apple", {"fruit", "red"})
    print(len(item.tags))
    print("fruit" in item.tags)
    item.tags.add("sweet")
    print(len(item.tags))
    print("sweet" in item.tags)

main()
