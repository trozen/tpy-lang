"""Intra-process async channel -- the first Send-marker enforcement site.

`channel[T: Send](cap)` returns a `(Sender[T], Receiver[T])` pair backed by
a fixed-capacity FIFO ring on the single-threaded executor. The `T: Send`
bound is the enforcement: a non-Send payload is rejected at the factory
call (Phase-2 bound machinery; no compiler-side code). See
`docs/CHANNEL_DESIGN.md`.

v1 is SPSC (single producer, single consumer): at most one parked sender
and one parked receiver, mirroring `Future`'s single-waiter model. Shared
state is `Rc`-backed (single-threaded; the cross-thread Arc-backed channel
is Phase 6).
"""
from tpy import Own, Int32, UInt32, nocopy, Send
from tpy.mem import UninitArrayStorage, UninitHeapStorage
from tpy.coro import Waker, Poll, poll_ready, poll_pending, poll_ready_none
from tplib.rc import Rc


class ChannelClosed(Exception):
    """Raised by `recv` once the channel is closed and drained, and by
    `send` once the receiving end is gone."""
    pass


@nocopy
class _ChanState[T: Send]:
    """Shared ring buffer + park slots. Reached only through `Rc` handles
    held by `Sender` / `Receiver`."""
    _buf: UninitHeapStorage[T]
    _cap: UInt32
    _head: UInt32
    _count: UInt32
    _closed: bool
    # SPSC: at most one parked waiter per side.
    _send_waker: Waker
    _has_send_waiter: bool
    _recv_waker: Waker
    _has_recv_waiter: bool

    def __init__(self, capacity: UInt32) -> None:
        self._buf = UninitHeapStorage[T](capacity)
        self._cap = capacity
        self._head = 0
        self._count = 0
        self._closed = False
        self._send_waker = Waker()
        self._has_send_waiter = False
        self._recv_waker = Waker()
        self._has_recv_waiter = False

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
        # Precondition: not full (the sole producer checked under SPSC).
        self._buf.init((self._head + self._count) % self._cap, value)
        self._count += 1
        if self._has_recv_waiter:
            self._has_recv_waiter = False
            self._recv_waker.wake()

    def _pop(self) -> Own[T]:
        # Precondition: not empty.
        value = self._buf.take(self._head)
        self._head = (self._head + 1) % self._cap
        self._count -= 1
        if self._has_send_waiter:
            self._has_send_waiter = False
            self._send_waker.wake()
        return value

    def _close(self) -> None:
        self._closed = True
        if self._has_recv_waiter:
            self._has_recv_waiter = False
            self._recv_waker.wake()
        if self._has_send_waiter:
            self._has_send_waiter = False
            self._send_waker.wake()


@nocopy
class _Recv[T: Send]:
    """Awaitable produced by `Receiver.recv()`. Ready with the popped value
    when data is present; raises `ChannelClosed` once closed and drained;
    parks the receiver otherwise."""
    _state: Rc[_ChanState[T]]

    def __init__(self, state: Own[Rc[_ChanState[T]]]) -> None:
        self._state = state

    # Cancellation is task-level (the awaiting Task throws CancelledError
    # before re-polling); the channel has no per-await state to flip.
    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[T]]:
        if not self._state._is_empty():
            return poll_ready(self._state._pop())
        if self._state._closed:
            raise ChannelClosed("recv on closed channel")
        self._state._recv_waker = waker
        self._state._has_recv_waiter = True
        return poll_pending()


@nocopy
class _Send[T: Send]:
    """Awaitable produced by `Sender.send(value)`. Holds the pending value
    across suspension; pushes it once space frees; raises `ChannelClosed`
    if the receiver is gone."""
    _state: Rc[_ChanState[T]]
    _value: UninitArrayStorage[T, 1]
    _has_value: bool

    def __init__(self, state: Own[Rc[_ChanState[T]]], value: Own[T]) -> None:
        self._state = state
        self._value = UninitArrayStorage[T, 1]()
        self._value.init0(value)
        self._has_value = True

    def __del__(self) -> None:
        # Drop the value if it was never pushed (closed channel / cancel).
        if self._has_value:
            self._value.take0()

    def cancel(self) -> None:
        pass

    def __poll__(self, waker: Waker) -> Own[Poll[None]]:
        if self._state._closed:
            raise ChannelClosed("send on closed channel")
        if not self._state._is_full():
            self._state._push(self._value.take0())
            self._has_value = False
            return poll_ready_none()
        self._state._send_waker = waker
        self._state._has_send_waiter = True
        return poll_pending()


@nocopy
class Sender[T: Send]:
    """The sending half. SPSC: not clonable in v1."""
    _state: Rc[_ChanState[T]]

    def __init__(self, state: Own[Rc[_ChanState[T]]]) -> None:
        self._state = state

    def __del__(self) -> None:
        # Backstop only: a forgotten close() still unblocks the receiver.
        # close() is the contract -- destructor timing is executor-driven
        # (see docs/CHANNEL_DESIGN.md "Destructor backstop").
        self._state._close()

    def send(self, value: Own[T]) -> Own[_Send[T]]:
        return _Send[T](self._state.clone(), value)

    def close(self) -> None:
        self._state._close()


@nocopy
class Receiver[T: Send]:
    """The receiving half."""
    _state: Rc[_ChanState[T]]

    def __init__(self, state: Own[Rc[_ChanState[T]]]) -> None:
        self._state = state

    def __del__(self) -> None:
        # Receiver gone -> the sender's next send sees a closed channel.
        self._state._close()

    def recv(self) -> Own[_Recv[T]]:
        return _Recv[T](self._state.clone())


def channel[T: Send](capacity: Int32) -> tuple[Own[Sender[T]], Own[Receiver[T]]]:
    if capacity < 1:
        raise ValueError("channel capacity must be >= 1")
    state = Rc.new(_ChanState[T](UInt32(capacity)))
    state_for_sender = state.clone()
    return (Sender[T](state_for_sender), Receiver[T](state))
