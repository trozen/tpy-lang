/**
 * TurboPython Runtime - Async primitives
 *
 * The async surface lives in TPy; see `lib/tpy/tpy/coro/__init__.py`
 * (Waker, Awaker) and `lib/tpy/asyncio/_executor.py` (Executor,
 * _ExecutorScope, _current_executor). The C++ side carries two pieces:
 * `CancelledError` (thrown into a coroutine at the resumed-await
 * position when its task is cancelled) and `poll_with_cancel`, the
 * resume-case helper template every async-def coro frame uses to
 * propagate outer cancellation through to the in-flight sub-coro
 * before polling.
 */

#pragma once

#include "core.hpp"

namespace tpy {

/**
 * CancelledError -- thrown into a coroutine at the resumed-await position
 * when its task is cancelled. Inherits BaseException (not Exception) so
 * `except Exception:` does not silently swallow it.
 */
struct CancelledError : BaseException {
    CancelledError() : BaseException("CancelledError") {}
    using BaseException::BaseException;
    TPY_THROWABLE_VIRTUALS(CancelledError)
};

/**
 * Resume-case helper: poll an async-def frame's sub-coroutine while
 * propagating outer cancellation through to it.
 *
 * Called from every `S_AFTER_AWAIT_N` block emitted by the async-def
 * codegen. If the outer's `__cancel_pending` flag is set on entry,
 * the helper delivers the cancel by clearing the flag, calling
 * `sub->cancel()`, then polling -- so the sub observes the cancel at
 * its own next suspension point and can run `finally`-with-await
 * cleanup. Three completion paths:
 *
 *  - Sub returns Pending and cancel was being delivered: re-set the
 *    outer's flag so the cancel persists until the next poll attempt
 *    actually delivers it.
 *  - Sub raises (CancelledError or another exception): unwinds with
 *    a clean cancel_pending state -- nested `finally`-with-await
 *    bodies in the outer don't get re-cancelled at every new
 *    suspension.
 *  - Sub returns Ready synchronously while we were delivering cancel:
 *    the sub raced past the cancel signal. Outer cancel still wins;
 *    throw `CancelledError`. (The sub's Ready value is discarded as
 *    the returned Poll's storage unwinds.)
 *
 * `Sub` is duck-typed: an `std::optional<T>` or `T*` whose pointee
 * provides `cancel()` and `__poll__(Waker) -> Poll<T>`. Both shapes
 * use `->`, so the same template body works for INLINE/ERASED and
 * BORROWED await modes. Inline so the compiler can fold the
 * not-cancelling fast path back to a plain `sub->__poll__(waker)`.
 */
template <typename Sub, typename W>
inline auto poll_with_cancel(Sub&& sub, bool& cancel_pending, W&& waker) {
    bool was_canceling = cancel_pending;
    if (was_canceling) {
        cancel_pending = false;
        sub->cancel();
    }
    auto r = sub->__poll__(waker);
    if (r.is_pending()) {
        if (was_canceling) cancel_pending = true;
        return r;
    }
    if (was_canceling) throw CancelledError();
    return r;
}

}  // namespace tpy
