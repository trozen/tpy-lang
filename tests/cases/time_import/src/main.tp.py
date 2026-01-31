from time import time

def main():
    t = time()
    # Verify timestamp is reasonable (after 2024: 1704067200)
    # This avoids exact output comparison that would fail due to timing
    if t > 1704067200:
        print("ok")
    else:
        print("error: timestamp too small")

main()
