/**
 * TurboPython Runtime - Async primitives
 *
 * The async surface lives in TPy; see `lib/tpy/tpy/coro/__init__.py`
 * (Waker, Awaker) and `lib/tpy/asyncio/_executor.py` (Executor,
 * _ExecutorScope, _current_executor). The only C++-side piece is
 * `CancelledError` -- thrown into a coroutine at the resumed-await
 * position when its task is cancelled.
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

}  // namespace tpy
