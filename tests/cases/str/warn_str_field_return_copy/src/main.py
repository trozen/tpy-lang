# Warn when method returns self.field where field is str (copies the string).
from tpy import StrView, String


class Item:
    name: str
    desc: str
    label: str

    def __init__(self, name: str, desc: str, label: str):
        self.name = name
        self.desc = desc
        self.label = label

    # Warning: -> str copies the field
    def get_name(self) -> str:
        return self.name  # tpyc: warning(/returns a copy of str field/)

    # No warning: -> StrView is zero-copy
    def get_desc(self) -> StrView:
        return self.desc

    # No warning: -> String is explicit owned
    def get_label(self) -> String:
        return self.label

    # No warning: dunder method
    def __str__(self) -> str:
        return self.name


def main():
    item = Item("widget", "a useful widget", "WDG-001")
    print(item.get_name())
    print(item.get_desc())
    print(item.get_label())
    print(item)


main()
