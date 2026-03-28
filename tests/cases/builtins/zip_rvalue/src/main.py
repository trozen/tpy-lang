# zip() with rvalue arguments (owning iterator prevents dangling)
from tpy import Own, Int32

def get_names() -> Own[list[str]]:
    return ["alice", "bob"]

def get_scores() -> Own[list[Int32]]:
    return [100, 200]

def main() -> None:
    for name, score in zip(get_names(), get_scores()):
        print(name, score)

main()
