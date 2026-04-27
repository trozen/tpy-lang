/**
 * TurboPython Runtime - Output sink dispatch
 *
 * Resolves a print()-style file= target to a std::ostream& usable with
 * the existing operator<< chain. Three concrete overloads bypass any
 * adapter (cout/cerr via std::ostream&, sys.stdout/stderr via StdStream,
 * open()-files via TextFile); a fallback template wraps any user type
 * satisfying the Writable concept in a streambuf adapter.
 */

#pragma once

#include <cstdint>
#include <ostream>
#include <streambuf>
#include <string_view>
#include <type_traits>

#include "file.hpp"
#include "system.hpp"

namespace tpy {

template <typename W>
concept WritableSink = requires(W& w, std::string_view sv) {
    { w.write(sv) } -> std::convertible_to<int32_t>;
    { w.flush() } -> std::same_as<void>;
};

namespace detail {

// Buffered streambuf forwarding to a Writable's write() / flush().
// sync() drains the buffer AND calls W::flush() (semantics for `<< std::flush`);
// the destructor only drains, matching std::ostream's no-flush-on-destroy.
template <typename W>
class WritableStreambuf final : public std::streambuf {
    W* w_;
    static constexpr std::size_t kBufSize = 256;
    char buf_[kBufSize];

    void drain() {
        const auto n = pptr() - pbase();
        if (n > 0) {
            w_->write(std::string_view(pbase(), static_cast<std::size_t>(n)));
            setp(buf_, buf_ + kBufSize);
        }
    }

protected:
    std::streamsize xsputn(const char* s, std::streamsize n) override {
        if (n <= 0) return 0;
        if (pptr() != pbase()) drain();
        // Mirrors Python's io.IOBase.write: short-write / error returns from
        // the user's write() are not propagated as a streambuf failure.
        w_->write(std::string_view(s, static_cast<std::size_t>(n)));
        return n;
    }

    int_type overflow(int_type ch) override {
        drain();
        if (!traits_type::eq_int_type(ch, traits_type::eof())) {
            *pptr() = traits_type::to_char_type(ch);
            pbump(1);
        }
        return traits_type::not_eof(ch);
    }

    int sync() override {
        drain();
        w_->flush();
        return 0;
    }

public:
    explicit WritableStreambuf(W& w) : w_(&w) {
        setp(buf_, buf_ + kBufSize);
    }

    ~WritableStreambuf() override { drain(); }

    WritableStreambuf(const WritableStreambuf&) = delete;
    WritableStreambuf(WritableStreambuf&&) = delete;
    WritableStreambuf& operator=(const WritableStreambuf&) = delete;
    WritableStreambuf& operator=(WritableStreambuf&&) = delete;
};

template <typename W>
class WritableOstream final : public std::ostream {
    WritableStreambuf<W> sb_;

public:
    explicit WritableOstream(W& w) : std::ostream(nullptr), sb_(w) {
        rdbuf(&sb_);
    }

    WritableOstream(const WritableOstream&) = delete;
    WritableOstream(WritableOstream&&) = delete;
    WritableOstream& operator=(const WritableOstream&) = delete;
    WritableOstream& operator=(WritableOstream&&) = delete;
};

} // namespace detail

inline std::ostream& as_ostream(std::ostream& os) { return os; }
inline std::ostream& as_ostream(StdStream& s)    { return s.sink(); }
inline std::ostream& as_ostream(TextFile& f)     { return f.sink(); }

template <typename W>
    requires WritableSink<W>
          && (!std::is_base_of_v<std::ostream, W>)
          && (!std::is_same_v<std::remove_cv_t<W>, StdStream>)
          && (!std::is_same_v<std::remove_cv_t<W>, TextFile>)
detail::WritableOstream<W> as_ostream(W& w) {
    return detail::WritableOstream<W>(w);
}

} // namespace tpy
