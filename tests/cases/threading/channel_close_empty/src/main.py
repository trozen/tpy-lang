# Explicit close() on an empty channel: with nothing ever sent, close() makes
# the next recv() see closed-and-empty and raise ChannelClosed at once (no
# block). Single-threaded, deterministic. Complements channel_close_recv
# (close after draining) and channel_mpsc (threaded, drop-based close).
#
# NB: uses explicit close(), not a drop-of-the-last-Sender, on purpose -- a
# named @nocopy Sender local moved into a helper to drop it early is destructed
# at the caller's scope end, not the helper's return (a filed drop-timing bug,
# BUGS.md), so a drop-based single-threaded form would not close here. Drop-
# based auto-close is covered by channel_mpsc (Sender in a spawned task's
# field, dropped at task completion).
from tpy import int32
from tplib.channel import channel, ChannelClosed


def main() -> None:
    tx, rx = channel[int32](4)
    tx.close()
    try:
        rx.recv()
        print("no raise")
    except ChannelClosed:
        print("closed empty")


main()
