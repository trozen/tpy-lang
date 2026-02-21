import time

def main():
    start = time.time()
    time.sleep(0.1)  # Sleep for 100ms
    end = time.time()
    elapsed = end - start
    # Check that at least 0.05 seconds passed (allowing for timer variance)
    if elapsed >= 0.05:
        print("ok")
    else:
        print("error: sleep too short")

main()
