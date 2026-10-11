"""scripts/git-hooks: commit-msg and pre-push reject a message carrying tool
attribution or a pattern from the gitignored `.commit-banned-words` list."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "scripts" / "git-hooks"
SCRIPTS = ("check-message", "commit-msg", "pre-push")
SCISSORS = "# ------------------------ >8 ------------------------"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _env(**extra: str) -> dict[str, str]:
    # An outer git (a hook running this suite) exports GIT_DIR and friends,
    # which would point every command here at the outer repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_EDITOR="true",
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
    )
    env.update(extra)
    return env


def _git(cwd: Path, *args: str, check: bool = True, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_env(**env), check=check, capture_output=True, text=True, errors="replace",
    )


def _install(root: Path) -> None:
    """Copies the hooks into the clone and links them as README says."""
    dest = root / "scripts" / "git-hooks"
    dest.mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy2(HOOKS / name, dest / name)
    for name in ("commit-msg", "pre-push"):
        (root / ".git" / "hooks" / name).symlink_to(f"../../scripts/git-hooks/{name}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _install(root)
    (root / ".gitignore").write_text("/.commit-banned-words\n")
    (root / ".commit-banned-words").write_text("")
    _git(root, "add", ".gitignore", "scripts")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _list(root: Path, data: str | bytes) -> None:
    path = root / ".commit-banned-words"
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)


def _run(cwd: Path, message: str | bytes) -> subprocess.CompletedProcess:
    """Runs the installed commit-msg hook on `message`, as git would."""
    msg = cwd / "MSG"
    if isinstance(message, str):
        msg.write_text(message)
    else:
        msg.write_bytes(message)
    common = Path(_git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    return subprocess.run(
        ["sh", str(common / "hooks" / "commit-msg"), str(msg)],
        cwd=cwd, env=_env(), capture_output=True, text=True, errors="replace",
    )


def _rejected(result: subprocess.CompletedProcess) -> bool:
    return result.returncode == 1 and "rejected" in result.stderr


# -- the checker ------------------------------------------------------------

def test_a_plain_message_passes(repo: Path) -> None:
    assert _run(repo, "thir: a fix\n\nWhy it was needed.\n").returncode == 0


# One test per built-in pattern, each line matching that pattern alone, so
# dropping any one of them fails a test.
def test_builtin_session_line(repo: Path) -> None:
    result = _run(repo, "thir: a fix\n\nClaude-Session: 0123\n")
    assert _rejected(result) and "Claude-Session: 0123" in result.stderr


def test_builtin_co_author_line(repo: Path) -> None:
    result = _run(repo, "thir: a fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")
    assert _rejected(result) and "Co-Authored-By" in result.stderr


def test_builtin_generated_with_line(repo: Path) -> None:
    result = _run(repo, "thir: a fix\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n")
    assert _rejected(result) and "Generated with" in result.stderr


def test_builtin_session_url(repo: Path) -> None:
    result = _run(repo, "thir: a fix\n\nsee https://claude.ai/code/session_x\n")
    assert _rejected(result) and "claude.ai/code" in result.stderr


@pytest.mark.parametrize("line", ["    Claude-Session: 0123", "\tCo-Authored-By: Claude <x@y>"])
def test_indented_attribution_rejects(repo: Path, line: str) -> None:
    assert _rejected(_run(repo, f"Squashed commit\n\n{line}\n"))


def test_a_human_co_author_passes(repo: Path) -> None:
    assert _run(repo, "fix: x\n\nCo-Authored-By: Ada <ada@example.org>\n").returncode == 0


def test_a_missing_list_rejects_every_message(repo: Path) -> None:
    (repo / ".commit-banned-words").unlink()
    result = _run(repo, "thir: a fix\n")
    assert _rejected(result)
    assert ".commit-banned-words" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_list_rejects_every_message(repo: Path) -> None:
    (repo / ".commit-banned-words").chmod(0)
    assert _rejected(_run(repo, "thir: a fix\n"))


def test_a_directory_in_place_of_the_list_rejects(repo: Path) -> None:
    (repo / ".commit-banned-words").unlink()
    (repo / ".commit-banned-words").mkdir()
    result = _run(repo, "thir: a fix\n")
    assert _rejected(result) and "create it there" in result.stderr


def test_a_malformed_pattern_rejects_every_message(repo: Path) -> None:
    _list(repo, "broken(\n")
    result = _run(repo, "thir: a fix\n")
    assert _rejected(result)
    assert "not a valid regex" in result.stderr


def test_a_listed_word_rejects_case_insensitively(repo: Path) -> None:
    _list(repo, "# private names\n#\n\n  # indented comment\nProjectX\n")
    assert _rejected(_run(repo, "docs: mention projectx\n"))
    assert _run(repo, "docs: mention nothing for #42\n").returncode == 0


def test_comment_lines_are_checked(repo: Path) -> None:
    _list(repo, "secret\n")
    assert _rejected(_run(repo, "fix: a thing\n# secret in a comment line\n"))


def test_commit_msg_stops_at_the_scissors_line(repo: Path) -> None:
    _list(repo, "secret\n")
    assert _run(repo, f"fix: a thing\n{SCISSORS}\n+the secret\n").returncode == 0
    assert _rejected(_run(repo, f"fix: the secret\n{SCISSORS}\n"))


@pytest.mark.parametrize("data", [
    pytest.param(b"secret\r\n", id="crlf"),
    pytest.param(b"\xef\xbb\xbfsecret\n", id="bom"),
    pytest.param(b"secret \t\n", id="trailing-space"),
    pytest.param(b"secret", id="no-final-newline"),
])
def test_list_line_endings_still_reject(repo: Path, data: bytes) -> None:
    _list(repo, data)
    assert _rejected(_run(repo, "fix: the secret\n"))
    assert _run(repo, "fix: plain\n").returncode == 0


def test_whitespace_only_list_lines_are_skipped(repo: Path) -> None:
    _list(repo, b"  \n\t\r\n")
    assert _run(repo, "fix: plain\n").returncode == 0


def test_a_banned_line_after_invalid_utf8_rejects(repo: Path) -> None:
    _list(repo, "secret\n")
    result = _run(repo, b"fix: x\n\xff\x00\nthe secret caf\xe9\n")
    assert _rejected(result) and "the secret" in result.stderr


def test_an_unset_variable_from_the_environment_is_ignored(repo: Path, tmp_path: Path) -> None:
    _list(repo, "secret\n")
    msg = tmp_path / "MSG"
    msg.write_text("fix: the secret\n")
    result = subprocess.run(
        ["sh", str(repo / ".git" / "hooks" / "commit-msg"), str(msg)],
        cwd=repo, env=_env(seen=str(repo / ".commit-banned-words")),
        capture_output=True, text=True, errors="replace",
    )
    assert _rejected(result)


def test_git_without_path_format_rejects(repo: Path, tmp_path: Path) -> None:
    # Git before 2.31 echoes the unknown `--path-format` option back.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    real = shutil.which("git")
    fake.write_text(
        "#!/bin/sh\n"
        'case "$*" in *--path-format=absolute*) printf -- "--path-format=absolute\\n.git\\n"; exit 0 ;; esac\n'
        f'exec "{real}" "$@"\n'
    )
    fake.chmod(0o755)
    msg = tmp_path / "MSG"
    msg.write_text("fix: plain\n")
    result = subprocess.run(
        ["sh", str(repo / ".git" / "hooks" / "commit-msg"), str(msg)],
        cwd=repo, env=_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}"),
        capture_output=True, text=True, errors="replace",
    )
    assert _rejected(result) and "2.31" in result.stderr


# -- linked worktrees -------------------------------------------------------

@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt dir"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "wt")
    return wt


def test_a_linked_worktree_reads_the_main_checkout_list(repo: Path, worktree: Path) -> None:
    _list(repo, "secret\n")
    assert _rejected(_run(worktree, "fix: leak the secret\n"))
    assert _run(worktree, "fix: nothing\n").returncode == 0


def test_a_worktree_list_adds_to_the_main_list(repo: Path, worktree: Path) -> None:
    _list(repo, "secret")
    _list(worktree, "local\n")
    assert _rejected(_run(worktree, "fix: a local word\n"))
    assert _rejected(_run(worktree, "fix: the secret\n"))
    assert _run(worktree, "fix: plain\n").returncode == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_worktree_list_rejects(repo: Path, worktree: Path) -> None:
    _list(worktree, "local\n")
    (worktree / ".commit-banned-words").chmod(0)
    assert _rejected(_run(worktree, "fix: plain\n"))


def test_a_dangling_worktree_list_link_rejects(repo: Path, worktree: Path) -> None:
    (worktree / ".commit-banned-words").symlink_to(worktree / "gone")
    assert _rejected(_run(worktree, "fix: plain\n"))


# -- real commits through the installed hook --------------------------------

def _commit(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return _git(cwd, "commit", "-q", "--allow-empty", *args, check=False, **env)


def test_commit_m_with_a_comment_line_rejects(repo: Path) -> None:
    _list(repo, "secret\n")
    assert _commit(repo, "-m", "fix: x\n\n#42 the secret").returncode != 0
    assert _commit(repo, "-m", "fix: x\n\n#42 plain").returncode == 0


def test_commit_f_with_a_comment_line_rejects(repo: Path, tmp_path: Path) -> None:
    _list(repo, "secret\n")
    (tmp_path / "m").write_text("fix: y\n\n# the secret\n")
    assert _commit(repo, "-F", str(tmp_path / "m")).returncode != 0


def test_commit_cleanup_verbatim_rejects(repo: Path, tmp_path: Path) -> None:
    _list(repo, "secret\n")
    (tmp_path / "m").write_text("fix: y\n\n# the secret\n")
    assert _commit(repo, "--cleanup=verbatim", "-F", str(tmp_path / "m")).returncode != 0


def test_merge_squash_of_an_attribution_commit_rejects(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "a").write_text("a\n")
    _git(repo, "add", "a")
    _git(repo, "commit", "-q", "--no-verify", "-m",
         "feat: a\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    _git(repo, "checkout", "-q", "master")
    _git(repo, "merge", "-q", "--squash", "side")
    result = _git(repo, "commit", "-q", "--no-edit", check=False)
    assert result.returncode != 0
    assert "    Co-Authored-By: Claude" in result.stderr


def test_commit_v_does_not_check_the_diff(repo: Path, tmp_path: Path) -> None:
    (repo / "f.py").write_text('URL = "https://claude.ai/code/x"\n')
    _git(repo, "add", "f.py")
    editor = tmp_path / "editor.sh"
    editor.write_text('#!/bin/sh\n{ echo "fix: add f"; cat "$1"; } >"$1.new" && mv "$1.new" "$1"\n')
    editor.chmod(0o755)
    result = _git(repo, "commit", "-q", "-v", check=False, GIT_EDITOR=str(editor))
    assert result.returncode == 0, result.stderr


# -- pre-push ---------------------------------------------------------------

@pytest.fixture
def remote(repo: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "master")
    return bare


def _push(repo: Path, *refs: str) -> subprocess.CompletedProcess:
    return _git(repo, "push", "-q", "origin", *refs, check=False)


def test_push_refuses_a_new_branch_with_a_banned_commit(repo: Path, remote: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "feat: x\n\nClaude-Session: 0123")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: y")
    result = _push(repo, "feature")
    assert result.returncode != 0
    assert "feat: x" in result.stderr and "Claude-Session: 0123" in result.stderr
    assert "feat: y" not in result.stderr
    assert _git(remote, "branch", "--list", "feature").stdout == ""


def test_push_accepts_a_clean_new_branch(repo: Path, remote: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: y")
    assert _push(repo, "feature").returncode == 0


def test_push_checks_an_update_of_an_existing_branch(repo: Path, remote: Path) -> None:
    _list(repo, "secret\n")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: clean")
    assert _push(repo, "master").returncode == 0
    _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "fix: the secret")
    result = _push(repo, "master")
    assert result.returncode != 0 and "the secret" in result.stderr
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: clean again")
    assert _push(repo, "master").returncode == 0


def test_push_checks_the_commit_a_tag_points_to(repo: Path, remote: Path) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "x\n\nClaude-Session: 0123")
    _git(repo, "tag", "-a", "-m", "release", "v1")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    assert _push(repo, "v1").returncode != 0


def test_push_checks_an_annotated_tag_message(repo: Path, remote: Path) -> None:
    _git(repo, "tag", "-a", "-m", "release\n\nClaude-Session: 0123", "v1")
    assert _push(repo, "v1").returncode != 0
    _git(repo, "tag", "-a", "-m", "release", "v2")
    assert _push(repo, "v2").returncode == 0


def test_push_of_a_deletion_passes(repo: Path, remote: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    assert _push(repo, "feature").returncode == 0
    assert _push(repo, ":feature").returncode == 0


def test_push_does_not_recheck_published_commits(repo: Path, remote: Path) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", "x\n\nClaude-Session: 0123")
    _git(repo, "push", "-q", "--no-verify", "origin", "master")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: clean")
    assert _push(repo, "master").returncode == 0
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: clean")
    assert _push(repo, "feature").returncode == 0
