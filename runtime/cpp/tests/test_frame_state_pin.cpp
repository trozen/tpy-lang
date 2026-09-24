/**
 * frame_state self-check: a resumable frame is never copied, never assigned,
 * and moved only while it has never been entered.
 *
 * No TPy program should reach the panic -- sema keeps every started frame
 * where it was built -- so the backstop is pinned here rather than by a case:
 *
 *   1. A frame spelled the way codegen spells one (a `frame_state __state`
 *      member beside ordinary fields) is not copyable and not assignable, so
 *      a copy that slips past sema is a C++ build error, never a snapshot of
 *      the generator that advances on its own.
 *   2. Moving an unentered frame works and leaves the source MOVED_FROM, so
 *      its destructor does not re-run pending cleanup.
 *   3. Moving a frame whose state is anything else panics, in every build
 *      mode.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <string>
#include <type_traits>
#include <utility>

#include <sys/wait.h>
#include <unistd.h>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

struct Frame {
    ::tpy::frame_state __state;
    int32_t i;
    std::string s;

    enum : int32_t { S_INITIAL = 0, S_RESUME_0 = 1, S_DONE = 2 };

    Frame() : __state(S_INITIAL), i(0) {}
};

static_assert(!std::is_copy_constructible_v<Frame>);
static_assert(!std::is_copy_assignable_v<Frame>);
static_assert(!std::is_move_assignable_v<Frame>);
static_assert(std::is_move_constructible_v<Frame>);

enum class Outcome { Returned, Panicked, Crashed };

// Runs `body` in a child process: a normal return exits 0, tpy_panic exits
// 1, and anything else (a signal, another code) is a crash, which neither
// expectation may accept.
template <typename F>
Outcome run_child(F body) {
    std::fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) {
        return Outcome::Crashed;
    }
    if (pid == 0) {
        // The panic message is expected; keep it out of the harness output.
        if (std::freopen("/dev/null", "w", stderr) == nullptr) {
            std::_Exit(2);
        }
        body();
        std::_Exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status)) {
        return Outcome::Crashed;
    }
    switch (WEXITSTATUS(status)) {
    case 0: return Outcome::Returned;
    case 1: return Outcome::Panicked;
    default: return Outcome::Crashed;
    }
}

}  // namespace

int main() {
    Frame a;
    a.s = "payload";
    Frame b(std::move(a));
    check(b.__state == Frame::S_INITIAL, "an unentered frame moves with its state");
    check(b.s == "payload", "an unentered frame moves its fields");
    check(a.__state == ::tpy::frame_state::MOVED_FROM,
          "the moved-from frame reads MOVED_FROM");
    Frame c(std::move(a));
    check(c.__state == ::tpy::frame_state::MOVED_FROM,
          "moving a moved-from frame is harmless");

    for (int32_t st : {int32_t(Frame::S_RESUME_0), int32_t(Frame::S_DONE)}) {
        check(run_child([st] {
                  Frame f;
                  f.__state = st;
                  Frame g(std::move(f));
                  (void)g;
              }) == Outcome::Panicked,
              "moving an entered frame panics");
    }
    check(run_child([] {
              Frame f;
              Frame g(std::move(f));
              (void)g;
          }) == Outcome::Returned,
          "moving an unentered frame does not panic");

    if (failures == 0) {
        std::printf("ok\n");
    }
    return failures == 0 ? 0 : 1;
}
