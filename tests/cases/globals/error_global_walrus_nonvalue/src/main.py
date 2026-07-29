# A walrus rebinding a REFERENCE-typed global is rejected with the same
# diagnostic the plain `items = [...]` assignment already produces, rather than
# silently writing a local shadow.

items = [1, 2]


def replace() -> int:
    global items
    return len(items := [3, 4])  # tpyc: error(/Cannot reassign global variable 'items' of non-value type/)


print(replace())
