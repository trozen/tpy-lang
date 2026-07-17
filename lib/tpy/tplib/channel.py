"""Blocking multi-producer / single-consumer channel over OS threads.

The Go-style channel: `channel[T: Send](cap)` returns a `(Sender[T],
Receiver[T])` pair backed by a fixed-capacity FIFO ring guarded by a
`Mutex`, with `send`/`recv` blocking the calling OS thread (via two
`Condvar`s). Unlike the async SPSC channel in `tpy.channel` (Rc-backed,
single-threaded, non-Send), the handles are `Arc`-backed and therefore
`Send` iff `T` is -- so they cross the `tpy.thread.spawn` boundary and a
real producer thread is the "goroutine".

Lives in `tplib` (not `tpy.thread`) because it composes `tplib.arc.Arc`:
the low-level `tpy.*` layer must not depend on the higher `tplib.*` layer.

v1 is multi-producer / single-consumer. Drop a `Sender` = "this producer
is done"; the last live sender's drop auto-closes the channel, and any
`close()` (on a sender, or the receiver going away) force-closes it
channel-wide. Select, unbuffered (rendezvous), and MPMC are out of scope.
See docs/CHANNEL_DESIGN.md.
"""
from __future__ import annotations
from typing import Iterator
from tpy import Own, Int32, UInt32, nocopy, Send
from tpy.mem import UninitHeapStorage
from tpy.sync import Mutex, Condvar
from tplib.arc import Arc


class ChannelClosed(Exception):
    """Raised by `recv` once the channel is closed and drained, and by
    `send` once the channel is closed."""
    pass


@nocopy
class _Buf[T: Send]:
    """The shared ring buffer + close/producer bookkeeping. All access is
    serialized by the enclosing `Mutex`, so the raw `UninitHeapStorage`
    slots are only ever touched under the lock."""
    _buf: UninitHeapStorage[T]
    _cap: UInt32
    _head: UInt32
    _count: UInt32
    _closed: bool
    _senders: UInt32

    def __init__(self, capacity: UInt32) -> None:
        self._buf = UninitHeapStorage[T](capacity)
        self._cap = capacity
        self._head = 0
        self._count = 0
        self._closed = False
        self._senders = 1

    def __del__(self) -> None:
        # UninitHeapStorage is uninitialized storage and won't drop live
        # slots itself -- drain the buffered elements or they leak.
        i: UInt32 = 0
        while i < self._count:
            self._buf.take((self._head + i) % self._cap)
            i += 1

    def _is_full(self) -> bool:
        return self._count >= self._cap

    def _is_empty(self) -> bool:
        return self._count == 0

    def _push(self, value: Own[T]) -> None:
        self._buf.init((self._head + self._count) % self._cap, value)
        self._count += 1

    def _pop(self) -> Own[T]:
        value = self._buf.take(self._head)
        self._head = (self._head + 1) % self._cap
        self._count -= 1
        return value


@nocopy
class _Chan[T: Send]:
    """The shared channel state reached only through `Arc` handles. Bundles
    the guarded ring with the two wait conditions: `_not_full` wakes a
    blocked sender when a slot frees, `_not_empty` wakes the receiver when
    an item lands or the channel closes."""
    _buf: Mutex[_Buf[T]]
    _not_full: Condvar
    _not_empty: Condvar

    def __init__(self, capacity: UInt32) -> None:
        self._buf = Mutex.new(_Buf[T](capacity))
        self._not_full = Condvar()
        self._not_empty = Condvar()


@nocopy
class Sender[T: Send]:
    """A producer handle. `clone()` mints another producer; the channel
    stays open until every `Sender` is dropped or someone calls `close()`."""
    _chan: Arc[_Chan[T]]

    def __init__(self, chan: Own[Arc[_Chan[T]]]) -> None:
        self._chan = chan

    def __del__(self) -> None:
        # This producer is done. Drop the sender count under the lock; the
        # last one closes the channel so the receiver's blocking recv wakes
        # and drains to completion instead of parking forever.
        c = self._chan.get()
        closed_now = False
        with c._buf.lock() as g:
            g.get()._senders -= 1
            if g.get()._senders == 0:
                g.get()._closed = True
                closed_now = True
        if closed_now:
            c._not_empty.notify_all()

    def clone(self) -> Own[Sender[T]]:
        c = self._chan.get()
        with c._buf.lock() as g:
            g.get()._senders += 1
        return Sender[T](self._chan.clone())

    def send(self, value: Own[T]) -> None:
        c = self._chan.get()
        with c._buf.lock() as g:
            while g.get()._is_full() and not g.get()._closed:
                c._not_full.wait(g)
            if g.get()._closed:
                raise ChannelClosed("send on closed channel")
            g.get()._push(value)
        c._not_empty.notify_one()

    def close(self) -> None:
        c = self._chan.get()
        with c._buf.lock() as g:
            g.get()._closed = True
        # Wake every parked side so blocked sends/recvs observe the close.
        c._not_empty.notify_all()
        c._not_full.notify_all()


@nocopy
class Receiver[T: Send]:
    """The single consumer handle. `recv()` blocks until an item is
    available (or raises `ChannelClosed` once closed and drained); iterating
    it drains the channel until close."""
    _chan: Arc[_Chan[T]]

    def __init__(self, chan: Own[Arc[_Chan[T]]]) -> None:
        self._chan = chan

    def __del__(self) -> None:
        # Consumer gone -> close so parked producers wake, see the close, and
        # raise ChannelClosed instead of blocking on a full buffer forever.
        c = self._chan.get()
        with c._buf.lock() as g:
            g.get()._closed = True
        c._not_full.notify_all()

    def recv(self) -> Own[T]:
        c = self._chan.get()
        with c._buf.lock() as g:
            while g.get()._is_empty() and not g.get()._closed:
                c._not_empty.wait(g)
            if g.get()._is_empty():
                raise ChannelClosed("recv on closed channel")
            value = g.get()._pop()
        c._not_full.notify_one()
        return value

    def __iter__(self) -> Iterator[Own[T]]:
        while True:
            try:
                yield self.recv()
            except ChannelClosed:
                return


def channel[T: Send](capacity: Int32) -> tuple[Own[Sender[T]], Own[Receiver[T]]]:
    if capacity < 1:
        raise ValueError("channel capacity must be >= 1")
    chan = Arc.new(_Chan[T](UInt32(capacity)))
    chan_for_recv = chan.clone()
    return (Sender[T](chan), Receiver[T](chan_for_recv))
