# A callee in another module, imported under another name by main.py.
def grow_then_read(events: list[int], tag: int, value: int) -> int:
    events.append(tag)
    return value
