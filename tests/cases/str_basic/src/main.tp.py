from tpy import Int32, Char

def count_char(text: str, target: Char) -> Int32:
    """Count occurrences of target character in text."""
    count: Int32 = 0
    i: Int32 = 0
    while i < len(text):
        if text[i] == target:
            count += 1
        i += 1
    return count

print(count_char("xoxox", "x"))
print(len("hello"))
print(chr(65))
