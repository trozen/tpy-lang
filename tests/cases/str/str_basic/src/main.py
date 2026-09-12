from tpy import int32, char

def count_char(text: str, target: char) -> int32:
    """Count occurrences of target character in text."""
    count: int32 = 0
    i: int32 = 0
    while i < len(text):
        if text[i] == target:
            count += 1
        i += 1
    return count

print(count_char("xoxox", "x"))
print(len("hello"))
print(chr(65))
