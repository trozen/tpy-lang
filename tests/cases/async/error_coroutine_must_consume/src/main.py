# A bare async-def call statement drops the coroutine unrun -- rejected.
# (Binding to a variable is legal; the guard rejects only genuine drops.)
async def sub() -> int:
    return 42

async def caller() -> int:
    sub()  # tpyc: error(/is dropped without running/)
    return 0

def main() -> None:
    print("ok")

main()
