# pytest-hosts

A pytest plugin that spreads one test session over the local machine and
one or more remote build hosts. It sits on top of pytest-xdist: xdist does
the scheduling, worker management and result collection; this plugin
turns a small config file into xdist's worker specs and adds what xdist
lacks for remote work (syncing the tree, a per-host job gate, forwarding
controller state, pulling written files back).

Nothing in it knows about any particular project. Project facts live in
the project's `pyproject.toml`; hosts live in a personal file.

## Usage

Plain `pytest`. When the personal hosts file exists and names this
project, the session is distributed. Otherwise nothing changes.

    pytest                      # distributed per config
    pytest -n 4                 # command-line -n: local only, no hosts
    pytest --hosts-local        # local only, config's local worker count
    PYTEST_HOSTS=0 pytest       # same, for a whole shell or a wrapper
    pytest --hosts-no-pull      # leave files the remote run wrote there
    pytest --hosts-no-wait      # fail instead of queueing on a busy host
    pytest --hosts-setup        # force the setup command this run
    pytest --hosts-only=NAME    # this run: only that host, no local workers

    pytest-hosts                # resolved config and the sub-commands
    pytest-hosts status         # reachability, slots, this checkout's trees
    pytest-hosts setup          # seed the trees without running tests
    pytest-hosts clean          # remove this checkout's trees, venvs, temp roots
    pytest-hosts config         # annotated reference for both config files

## Configuration

Two files. The personal one defines hosts once and says which projects
use them; the project one carries facts every developer needs.
`pytest-hosts config` prints both with every key and its default, and an
unknown key is rejected with the list of valid ones.

`~/.config/pytest-hosts/hosts.toml`:

    [local]
    workers = 8                 # local cap for every project ("auto" allowed)

    [hosts.bigbox]
    ssh = "bigbox"              # an ssh alias; user/key/port come from ~/.ssh/config
    workers = 60
    slots = 1                   # concurrent sessions this host accepts
    root = "~/.pytest-hosts"    # where synced trees and venvs live
    unreachable = "error"       # or "local": run without this host, loudly
    lock_dir = "/tmp/pytest-hosts"   # slot lock files; shared by every user of the box
    # tmp = "/scratch"                   # workers' temp root (absolute or ~); host TMPDIR when unset
    # ssh_config = "~/.ssh/bigbox.cfg"   # replaces the generated ssh config for this host
    # path_prepend = "/opt/toolchain/bin" # in front of PATH for workers and setup

    [projects.my-project]       # key: the [project] name in pyproject.toml
    local = 4
    hosts.bigbox = { workers = 40 }   # naming a host selects it; body overrides
    hosts.other = {}

`pyproject.toml` of the project:

    [tool.pytest-hosts]
    setup = "uv sync -q"                  # run in the synced tree via bash -lc
    setup_when = ["pyproject.toml", "uv.lock"]  # re-run setup when these change
    env = []                              # controller env vars forwarded to remote workers
    ignore = ["examples", "tmp"]          # sync ignores on top of .gitignore
    pull = true                           # pull back files the remote run wrote

Rules:

- A project with no `[projects.<name>]` entry runs locally, with one
  warning line naming the entry to add. Remote use is opt-in per project.
- `-n` typed on the command line means local only. `-n` from `addopts`
  is replaced by the config's `local` value.
- `--hosts-local` and `PYTEST_HOSTS=0` (also `false`, `no`, `off`) mean
  local only, with the config's `local` count: no probe, no sync, no
  slot taken. Unlike `-n` they say what is meant rather than a worker
  count. `-n` on the command line still wins over both.
- `--hosts-only=NAME` keeps that host alone and drops the local workers
  to zero. Naming a host the project does not use is a usage error, and
  so is naming one that turns out unreachable, whatever its
  `unreachable` policy says: an explicit request outranks a degrade.
  Combining it with `--hosts-local` or `PYTEST_HOSTS=0` is a usage error
  too, since the two ask for opposite things.
- `--dist` defaults to `load` and is left alone when given.
  `--maxprocesses` caps the local share only; per-host counts come from
  the config.
- Two hosts must not share one `ssh` alias (rejected at load): the
  session tells them apart by it.
- The slot gate assumes a build box whose users trust each other: the
  lock dir is world-writable, so any user can hold a slot, and the
  project's setup command runs on the host through a login shell. A
  per-user `lock_dir` opts out of sharing the gate.
- Per-project selection is keyed on the project name, not the checkout,
  so every worktree and clone shares one entry. A gitignored
  `.pytest-hosts.toml` in the checkout is honoured as a last-layer
  override for one-off experiments.

## What a session does

Every line the plugin prints is prefixed `hosts|` and kept short. A
distributed session starts with a pointer to the `pytest-hosts` command,
then how many local workers it keeps and the switch that would keep the
run here; a local-only run says why (the switch given, or a project
entry that selects no hosts); a run the plugin left alone prints nothing
beyond the missing-entry warning from the Rules above, which is an
ordinary pytest config warning.

1. Probe each host: one ssh command with a short connect timeout that
   prints `$HOME`, the non-interactive `$PATH` and the host's temp dir.
   The home makes a `~` root absolute (execnet chdirs without expanding
   it); the PATH is what a configured `path_prepend` goes in front of
   for the workers; the temp dir is where their temp roots go. The
   probe also opens the ControlMaster the rest of the session rides on.
   Unreachable: error, or, when the host says `unreachable = "local"`,
   a loud line and the run goes on without that host (the other hosts
   and the local workers stay).
2. Take one slot: `flock` on `<lock_dir>/<n>` (default `/tmp/pytest-hosts`,
   so it gates across users of one box), held by an ssh session for the
   whole run. All slots busy: print `bigbox busy, queued...` every 30s
   and wait. The whole run waits; xdist cannot add workers mid-session.
   A slot command that gives no answer within a minute fails the host
   instead: that is a broken connection, not a busy box. Every later
   ssh or rsync round trip (sync, setup, the pull-back) is capped at
   thirty minutes for the same reason.
3. Sync the checkout with `rsync -a --delete` over an ssh ControlMaster,
   using the repo's gitignore rules as filters, into
   `<root>/<hash8(source hostname + absolute path)>/<repo>`. Two
   worktrees or two source machines never share a tree. The tree keeps
   the checkout's own directory name because xdist rewrites path args to
   `<name>/<rel>` relative to the spec's `chdir`, so the hash goes on the
   parent, not the tree. Hosts sync in parallel.
4. Run the setup command through `bash -lc` inside the tree, with
   `UV_PROJECT_ENVIRONMENT` pointing at the sibling venv
   (`<root>/<hash8>/venv`, outside the tree), when that venv is missing,
   when the setup command or any `setup_when` file changed since the
   last run, or under `--hosts-setup`. Then touch a stamp file beside
   the tree: what setup wrote into the tree (a lockfile) is not the
   run's output.
5. Build the xdist specs: `local` popen workers plus `workers` direct
   `ssh=` specs per host with the remote venv's python (or `python3` from
   the ssh session's PATH when the project has no setup command) and
   `chdir` set to the tree's parent (`<root>/<hash8>`); a `path_prepend`
   becomes each worker's `PATH`, and each worker gets its own temp root
   (`<tmp>/pytest-hosts-<hash8>/w<n>`, via `PYTEST_DEBUG_TEMPROOT`):
   xdist hands a local worker its own basetemp but an ssh worker nothing,
   and many workers sharing one `pytest-of-<user>` dir race each other's
   cleanup. `pytest-hosts clean` removes the temp roots with the tree.
   In `pytest_xdist_setupnodes`
   the checkout is appended to xdist's rsync roots (so its path
   rewriting keeps working) and marked as already synced for every spec.
   xdist would still ship its own `pytest` package to each worker, one
   serial transfer per worker; when the tree's venv holds the
   controller's pytest version (step 4 reports it) that root is marked
   synced too and the tree's copy is used. Values of the `env` list are
   read in the same hook and injected into each remote spec.
6. Session end, also on Ctrl-C and internal errors (best effort): list
   files under the tree newer than the stamp, drop the ones the
   checkout's `git check-ignore` covers (outside a git checkout nothing
   is dropped), rsync that list back unconditionally, print each path.
   A file the remote run wrote wins over the local copy, with a warning
   when the local file was also modified after the session started.
   Files the run did not write are never touched. Release the slot.
7. Terminal summary, each line prefixed `hosts|`: the local worker
   count of tests, then one line per host with the tests it ran and the
   files pulled back, or `pull-back FAILED`; a dropped host says why.

## Constraints learned the hard way

- Workers must collect identical test lists; discovery that depends on
  directory order breaks on a different filesystem.
- xdist ships only the typed invocation args to workers and rewrites
  each existing path against its rsync roots; relative paths fail, and
  with no path typed a remote worker takes its chdir as rootdir (the
  ini file is a level down) and collects different ids. The plugin
  absolutises typed paths and, when none were typed, appends the paths
  pytest settled on (testpaths or the invocation dir), absolute.
- xdist's own rsync is deprecated and removed in 4.0; `pytest-xdist<4`
  is pinned and the sync is ours. Revisit when 4.0 ships.
- execnet's rsync deletes everything under the target it did not send,
  so the venv lives beside the tree, never inside it.
- Proxy fan-out (`--px` with `via=`) deadlocks on execnet's main-thread
  execmodel and a threaded proxy runs out of file descriptors. Direct
  `N*ssh=` specs work at 60 workers.
- sshd allows 10 sessions per connection (`MaxSessions`); past that, ssh
  falls back to a fresh connection per worker and says so on stderr each
  time. Each worker spec therefore names the control socket of its
  group, eight workers per master connection, so sixty workers ride on
  eight connections and stay under the limit.
- A remote worker's cwd is the tree's parent (the spec's `chdir`, which
  xdist's path rewriting needs), while its rootdir is the tree. A local
  worker's cwd is the checkout. Tests that touch files relative to cwd
  see the difference; tests that go by rootdir or `__file__` do not.
- A worker's config is rebuilt from the typed command-line args alone:
  option values the controller sets in its own `config.option` never
  reach a worker, and controller `os.environ` reaches the local popen
  workers only. A switch a worker needs must therefore be a typed arg
  (tpy's harness turns its `UPDATE_EXPECTED` env var into the
  `--update-snapshots` flag on the controller for that reason), or an
  env value forwarded by explicit list. Forward values, never resolved
  paths: a compiler path resolved on the controller means nothing on
  the host. tpy's `--cxx` is resolved by name on every worker, so a
  session is one toolchain everywhere or fails on the host lacking it;
  a bare `CXX=` in the environment reaches the local workers only, so
  use `--cxx`, or list `CXX` under `env` to forward it.
- Per-host caches stay per host. tpy's exec-result markers live under
  `~/.cache` on whichever machine ran the case, so a case that ran
  remotely is not cached locally next time, and the "skipped via cache"
  tally moves with the split. The pull-back covers the tree only.
- The pull-back lists files newer than a stamp touched on the host after
  the sync, while synced files keep their controller-side mtimes. A
  controller clock running ahead of the host's by more than the sync
  takes would make synced files look written and pull the whole tree
  back; not observed, not guarded.

## Testing

    uv run pytest tools/pytest-hosts/tests      # from the tpy checkout
    PYTEST_HOSTS_TEST_HOST=bigbox uv run pytest tools/pytest-hosts/tests

The suite is not part of tpy's default run: its end-to-end cases start
nested pytest sessions with rsync and flock, which would sit on the long
pole of the compiler suite. Unit tests cover config layering, name
resolution, spec generation, `-n` precedence, the command lines the
session runs, the slot gate against a fake holder process, the pull-back
filter and the ignore derivation, with no network. A fake `ssh` on PATH
that runs its command locally (and drops the one variable the tests
forward, so forwarding is really tested) lets the whole session -- probe,
slot gate, rsync, setup, execnet workers, pull-back -- run end to end
against this machine, one and two hosts. One integration test runs
against a real host named by `PYTEST_HOSTS_TEST_HOST` and is skipped
otherwise.

## Possible follow-ups

Startup of a trivial run against sixty remote workers costs about
fourteen seconds before the first test, measured on a real host:

- Five seconds of prepare, of which rsync's walk over an unchanged tree
  of 32k tracked files is three. A local change detector (git status
  plus mtimes) could skip the sync, at the price of a cache that can go
  stale.
- Nine seconds of gateway creation: xdist's node manager creates its
  workers one after another, and a remote one costs 0.15s (0.05s is the
  floor for an ssh session plus python startup; the rest is execnet
  bootstrapping itself over the channel). Creating them in parallel
  would need an override of `NodeManager.setup_nodes`; the spike found
  execnet fragile under threads, so prove it on a scratch script first.
- xdist processes the worker-ready events only after that loop ends, so
  the workers' own start-up (conftest import, toolchain probing) is
  hidden behind it today and would surface once the loop is parallel.

Neither matters for a full suite; both hurt a small `-k` run, where
`--hosts-local` is the workaround.

A failed pull-back is reported (`pull-back FAILED` in the summary) but
does not change the session's exit status, so a snapshot-updating run
whose results never arrived still looks green to a script. Making it a
failing session is a behaviour choice not yet taken.

## Not in v1

- Per-test host affinity (pinning a test to local or to a host): xdist's
  load scheduler has no such notion and nothing needs it yet.
- Losing a host mid-session: its tests go through xdist's crashed-node
  path and the plugin does not re-plan; the session ends the way an
  xdist session with a dead worker does.

## Packaging

- The plugin is its own package under `tools/pytest-hosts/` (own
  `pyproject.toml`, `pytest11` entry point, `pytest-hosts` console
  script), a uv workspace member that tpy pulls in as a dev dependency.
  It is generic: nothing in it imports from tpy or knows about
  `tests/cases`; anything project-specific goes through the config.
  When a second project wants it, the directory moves to its own repo
  and tpy's dependency line changes from workspace to git URL.
- Dependencies: `pytest`, `pytest-xdist>=3.5,<4`, `execnet`; `tomllib`
  from the stdlib.
- The generated ssh config lives in the user's runtime dir, starts with
  `Include ~/.ssh/config` (so anything the user's config sets per host
  wins) and adds `ControlMaster auto`, a `ControlPath` beside it,
  `ControlPersist`, `BatchMode yes`. A per-host `ssh_config` replaces it.
- xdist touch points: `config.option.tx` and `config.option.dist` are
  set in `pytest_cmdline_main`, after xdist's own (which is `tryfirst`);
  `config.invocation_params.args` tells a command-line `-n` from one in
  `addopts` and is rewritten with absolute path args; a `tryfirst`
  `pytest_sessionstart` prepares the hosts before `DSession` builds its
  nodes; in `pytest_xdist_setupnodes` the node manager is reached
  through the `dsession` plugin, the checkout is appended to
  `nodemanager.roots`, `(spec, root)` is added to
  `nodemanager._rsynced_specs` (the one underscored attribute used) and
  forwarded env values are set on each remote `spec.env`; when the
  tree's pytest is used, `<parent>/pytest` and `<parent>/_pytest` (copies
  an earlier controller shipped there) are removed on the host, the one
  destructive remote command outside `clean`; a `trylast`
  `pytest_sessionfinish` pulls back and releases after the workers are
  torn down; the per-host tally reads `report.node`, which `DSession`
  sets on every report it relays.
