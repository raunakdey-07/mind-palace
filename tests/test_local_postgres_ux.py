"""The local PostgreSQL experience, asserted rather than described.

Three defects came out of one real run on a laptop that had been rebooted.

1. **A refused connection bypassed the guidance handler.** `MindPalace(...)`
   is constructed outside the `try` that turns failures into advice, and its
   constructor connects to the database immediately. So a dead port produced a
   130-line `asyncio`/`sqlalchemy`/`asyncpg` stack trace instead of a sentence
   telling the operator what to do.

2. **A stopped container stayed stopped.** The container survived the reboot
   in `Exited` state, and the operator had to know that `podman ps -a` was the
   thing to run. The product knows the container name and the port; it should
   not need the operator to remember either.

3. **`podman-compose` refused to start.** The compose file demanded
   `${MIND_PALACE_RUNTIME_PASSWORD:?...}`, which `podman-compose` evaluates
   eagerly across every service -- so the file could not start the one
   service that had no secret in it.

And one gap the same run exposed: a bare `mindpalace` printed "Missing
command." and a usage line, which is a statement about the user's typing
rather than about the product.

These tests assert the properties, not the implementation. Where a test
needs a container CLI, it supplies a fake one: a real `podman` on the
developer's machine is not a test fixture, and a test that only passes on
the machine that wrote it is not a test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]

#: A port nothing listens on. Not 5432: the point of these tests is that the
#: *configured* port is unreachable, and 5432 is reachable on the machine
#: that runs them.
DEAD_PORT = 5499

DEAD_DATABASE_URL = f"postgresql://mpadmin:secret@127.0.0.1:{DEAD_PORT}/mindpalace"


def _invoke(args, env: dict | None = None):
    """Run the CLI in-process with a controlled environment.

    Only for the cases that do not depend on *which* database is
    configured. `api.services.db` reads `DATABASE_URL` once, when it is first
    imported, and every connection afterwards goes to that address -- so a
    test that changes the URL mid-process is testing the first URL it saw.
    The tests that need a specific unreachable database run in a subprocess
    instead, which is also what a person actually experiences.
    """
    from cli.main import app

    environment = dict(os.environ)
    environment.pop("MIND_PALACE_RUNTIME", None)
    environment["MIND_PALACE_RUNTIME"] = "0"
    if env:
        environment.update(env)
    return CliRunner().invoke(app, args, env=environment)


def _run_cli(args, database_url: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in its own process, against a specific database.

    A separate process is the only way to point the CLI at a different
    database: the engine is built once, from the environment, when the
    database module is first imported. It is also the honest way to assert
    "no traceback", because `CliRunner` catches the exception that a real
    process would have printed.
    """
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "MIND_PALACE_RUNTIME": "0",
    }
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "cli.main", *args],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
        env=environment,
    )


# --------------------------------------------------------------------------
# 1. A refused connection becomes guidance, not a traceback.
# --------------------------------------------------------------------------


def test_ingest_repo_reports_an_unreachable_database_without_a_traceback(tmp_path):
    """The regression: the constructor's connection failure reached the user raw.

    `ingest-repo` builds its client *before* the `try` block, and the
    constructor connects. So the port was refused outside the handler that
    renders guidance, and the operator got the raw driver stack.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nThe datastore is PostgreSQL.\n", encoding="utf-8")

    done = _run_cli(
        ["ingest-repo", str(docs), "--corpus", "unreachable-db"],
        DEAD_DATABASE_URL,
        {"MIND_PALACE_INGEST_ROOTS": str(tmp_path)},
    )

    assert done.returncode != 0
    output = done.stdout + done.stderr
    # No Python traceback. Not "no traceback for this one line" -- no
    # `Traceback (most recent call last)` at all, and none of the frames that
    # made the original 130 lines.
    assert "Traceback (most recent call last)" not in output
    assert "asyncpg" not in output
    assert "sqlalchemy" not in output.lower()
    # And the operator is told what to do about it.
    assert "database" in output.lower()
    assert "5432" in output or "127.0.0.1" in output


def test_recall_reports_an_unreachable_database_without_a_traceback():
    """The same guarantee for the command a newcomer reaches for first."""
    done = _run_cli(["recall", "what is the datastore"], DEAD_DATABASE_URL)

    assert done.returncode != 0
    output = done.stdout + done.stderr
    assert "Traceback (most recent call last)" not in output
    assert "asyncpg" not in output
    assert "database" in output.lower()


def test_the_failure_is_a_few_lines_and_not_a_wall_of_text():
    """A stack trace is long because it says little. This one says what to do.

    The bound is deliberately loose -- eight lines -- because the requirement
    is "readable at a glance", not "exactly three lines". A traceback of 130
    lines fails it for the same reason a seven-line wall of prose would.
    """
    done = _run_cli(["recall", "what is the datastore"], DEAD_DATABASE_URL)

    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    assert len(lines) <= 8, done.stdout + done.stderr


def test_a_rejected_password_says_the_password_is_wrong():
    """A wrong password and a closed port look alike to a driver.

    They do not look alike to a person: one is fixed by changing a value, the
    other by starting a service. Telling somebody to start the database when
    the database is already running sends them the wrong way.
    """
    done = _run_cli(
        ["recall", "what is the datastore"],
        "postgresql://mpadmin:wrong-password@localhost:5432/mindpalace",
    )

    assert done.returncode != 0
    output = done.stdout + done.stderr
    assert "Traceback (most recent call last)" not in output
    assert "authentication" in output.lower()
    assert "password" in output.lower()


def test_a_refused_connection_does_not_say_the_password_is_wrong():
    """The control: the two failures must not be confused.

    A message that mentioned the password for every failure would be a
    message nobody could act on.
    """
    done = _run_cli(["recall", "what is the datastore"], DEAD_DATABASE_URL)

    assert done.returncode != 0
    assert "password" not in (done.stdout + done.stderr).lower()


def test_debug_mode_still_shows_the_traceback():
    """The escape hatch has to actually escape.

    Without it, a developer who wanted the driver stack would have no way to
    get it, and the fix would be "hide the information" rather than "order the
    information".
    """
    done = _run_cli(["recall", "what is the datastore", "--debug"], DEAD_DATABASE_URL)

    assert done.returncode != 0
    assert "Traceback (most recent call last)" in done.stdout + done.stderr


def test_the_debug_environment_variable_also_shows_the_traceback():
    """The setting is one thing with two spellings, not two settings.

    A flag on the command and an environment variable for the process have to
    agree, or a script that sets the variable and a person who types the flag
    get different behaviour from the same product.
    """
    done = _run_cli(
        ["recall", "what is the datastore"],
        DEAD_DATABASE_URL,
        {"MIND_PALACE_DEBUG": "1"},
    )

    assert done.returncode != 0
    assert "Traceback (most recent call last)" in done.stdout + done.stderr


def test_verbose_adds_the_detail_without_the_stack():
    """`--verbose` is one line of detail. `--debug` is the stack.

    Two flags for two audiences: a person reading the error wants the
    exception's message, a person debugging the product wants the frames. A
    person who asked for the first should not get a hundred lines of the
    second.
    """
    done = _run_cli(["recall", "what is the datastore", "--verbose"], DEAD_DATABASE_URL)

    output = done.stdout + done.stderr
    assert done.returncode != 0
    assert "Traceback (most recent call last)" not in output
    assert "MemoryClientError" in output


def test_init_reports_an_unreachable_database_without_a_traceback():
    """`init` is the first command a newcomer runs, so it is the one that
    has to be readable when the database is not there.

    It used to append the driver's exception class and message to the
    guidance unconditionally. An exception class name is not an answer to
    "why is nothing happening", and it was the only place in the CLI that
    showed driver detail without being asked.
    """
    done = _run_cli(["init"], DEAD_DATABASE_URL)

    output = done.stdout + done.stderr
    assert done.returncode != 0
    assert "Traceback (most recent call last)" not in output
    assert "ConnectionRefusedError" not in output
    assert "could not connect to PostgreSQL" in output


# --------------------------------------------------------------------------
# 2. A stopped local container is found and started.
# --------------------------------------------------------------------------


def _fake_container_cli(tmp_path: Path, containers: list[dict], calls: Path) -> Path:
    """Write a fake `podman` that reports ``containers`` and records its calls.

    A real container CLI is not available on every machine that runs the
    suite, and a test that shells out to one is testing the machine rather
    than the code. The fake speaks the same JSON shape `podman ps --format
    json` does.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "podman"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"containers = {containers!r}\n"
        f"calls = {str(calls)!r}\n"
        "argv = sys.argv[1:]\n"
        "with open(calls, 'a') as handle:\n"
        "    handle.write(' '.join(argv) + '\\n')\n"
        "if argv[:1] == ['ps']:\n"
        "    print(json.dumps(containers))\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return bindir


def _exited_pgvector() -> list[dict]:
    """What `podman ps -a` reports for the container this product creates."""
    return [
        {
            "Names": ["mindpalace_postgresql_1"],
            "State": "exited",
            "Status": "Exited (0) 2 minutes ago",
            "Image": "docker.io/ankane/pgvector:latest",
            "Ports": [
                {
                    "host_ip": "",
                    "container_port": 5432,
                    "host_port": 5432,
                    "range": 1,
                    "protocol": "tcp",
                }
            ],
        }
    ]


def test_a_stopped_local_postgres_container_is_found(monkeypatch, tmp_path):
    """The detection: an `Exited` pgvector container publishing 5432.

    This is the container the product itself created -- the one named in
    `docker-compose.yml` -- so the name is not a heuristic here, it is the
    name the product chose.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, _exited_pgvector(), calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "db_ready", lambda *a, **k: False)
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: False)

    found = postgres.find_stopped_postgres_container(5432)

    assert found is not None, "the stopped container was not detected"
    assert found["name"] == "mindpalace_postgresql_1"
    assert found["state"] == "exited"


def test_a_running_container_is_not_a_candidate(monkeypatch, tmp_path):
    """The control: a healthy database must not be restarted.

    The detection looks for a stopped container. If it matched a running one
    it would restart a working database on every command, which is a worse
    failure than the one being fixed.
    """
    from cli import postgres

    running = _exited_pgvector()
    running[0]["State"] = "running"
    running[0]["Status"] = "Up 2 minutes (healthy)"

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, running, calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])

    assert postgres.find_stopped_postgres_container(5432) is None


def test_a_container_on_another_port_is_not_a_candidate(monkeypatch, tmp_path):
    """The control: a different port is a different database.

    "5432" is a substring of "15432". A container publishing 15432 is not
    the one publishing 5432, and starting it would not put the configured
    database where the client is looking for it.
    """
    from cli import postgres

    elsewhere = _exited_pgvector()
    elsewhere[0]["Ports"] = [
        {
            "host_ip": "",
            "container_port": 5432,
            "host_port": 15432,
            "range": 1,
            "protocol": "tcp",
        }
    ]

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, elsewhere, calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])

    assert postgres.find_stopped_postgres_container(5432) is None


def test_a_stopped_container_is_started_when_the_port_is_down(monkeypatch, tmp_path):
    """The behaviour: the port is down, so the stopped container is started.

    The container CLI is faked, so the assertion is about the decision and
    the command issued, not about the state of the machine.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, _exited_pgvector(), calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: False)

    def _ready(*_args, **_kwargs):
        """Ready only once the container has actually been started."""
        return calls.exists() and "start" in calls.read_text(encoding="utf-8")

    monkeypatch.setattr(postgres, "db_ready", _ready)

    started = postgres.ensure_local_postgres()

    assert started is True
    recorded = calls.read_text(encoding="utf-8")
    assert "start mindpalace_postgresql_1" in recorded, recorded


def test_a_running_database_is_left_alone(monkeypatch, tmp_path):
    """The control for the whole feature: nothing is started when it works.

    Without this, the auto-start would fire on every command against a
    healthy database.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, _exited_pgvector(), calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "db_ready", lambda *a, **k: True)
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: True)

    assert postgres.ensure_local_postgres() is True
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "start" not in recorded, recorded


def test_a_remote_database_is_never_touched(monkeypatch, tmp_path):
    """Only localhost is auto-recovered.

    A deployment whose database is on another host must not have a local
    container started for it, and must not be reported as a local failure.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, _exited_pgvector(), calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "db_ready", lambda *a, **k: False)
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://mpadmin:secret@db.internal:5432/mindpalace")

    postgres.ensure_local_postgres()

    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "start" not in recorded, recorded


def test_a_missing_container_is_provisioned_with_a_restart_policy(monkeypatch, tmp_path):
    """`mindpalace init` is one command, so it has to be able to create the database.

    There is no stopped container to start on a machine that has never run
    Mind Palace, so `init` provisions one. The `--restart unless-stopped`
    flag is what stops the original defect from coming back after the next
    reboot, so it is asserted rather than assumed.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, [], calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "db_ready", lambda *a, **k: False)
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://mpadmin:secret@127.0.0.1:5432/mindpalace")

    postgres.provision_local_postgres(timeout=0.1)

    recorded = calls.read_text(encoding="utf-8")
    assert "run" in recorded, recorded
    assert "--restart" in recorded and "unless-stopped" in recorded, recorded
    assert "pgvector" in recorded, recorded


def test_a_database_on_another_port_is_never_provisioned(monkeypatch, tmp_path):
    """The control: only the standard local port gets a container.

    Provisioning on whatever port `DATABASE_URL` happens to name is a
    surprise with a lasting consequence: a container nobody asked for, on a
    port that was chosen for something else. A database on any other port is
    one the operator runs.
    """
    from cli import postgres

    calls = tmp_path / "calls.log"
    bindir = _fake_container_cli(tmp_path, [], calls)

    monkeypatch.setattr(postgres, "_container_clis", lambda: [str(bindir / "podman")])
    monkeypatch.setattr(postgres, "db_ready", lambda *a, **k: False)
    monkeypatch.setattr(postgres, "tcp_open", lambda *a, **k: False)
    monkeypatch.setenv(
        "DATABASE_URL", f"postgresql://mpadmin:secret@127.0.0.1:{DEAD_PORT}/mindpalace"
    )

    postgres.provision_local_postgres(timeout=0.1)

    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "run" not in recorded, recorded


# --------------------------------------------------------------------------
# 3. `podman-compose` can start the file.
# --------------------------------------------------------------------------


def test_the_compose_file_demands_no_variable():
    """`podman-compose` evaluates `${VAR:?...}` eagerly, across every service.

    It is not a lazy interpolation the way `docker compose` treats it in some
    positions, so a required variable that only the `backend` service uses
    stops the `postgresql` service from starting at all. The file must have
    no required variables for that reason, and the local defaults have to
    exist for the same reason.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    # The check is on the file's *values*, not on the text: a comment that
    # explains why the required form was removed mentions it, and a comment
    # is not an interpolation.
    values = [line for line in compose.splitlines() if not line.lstrip().startswith("#")]
    offending = [line for line in values if ":?" in line]
    assert not offending, (
        "docker-compose.yml still uses a required-variable reference; "
        f"podman-compose evaluates it eagerly and refuses to start: {offending}"
    )


def test_the_postgres_service_restarts_after_a_reboot():
    """The container that was `Exited` after a reboot must come back by itself.

    `restart: unless-stopped` is the setting that means "start again after
    the machine reboots". Without it, the operator's first command after
    every reboot fails, which is the defect this whole change exists for.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    postgresql = compose.split("postgresql:", 1)[1].split("\n  ollama:", 1)[0]
    assert "restart: unless-stopped" in postgresql, postgresql


def test_the_compose_file_still_declares_a_runtime_password():
    """The default must be a *default*, not a deletion.

    The `backend` service still authenticates as a least-privileged role, so
    the variable has to keep being read -- it just may not be required.
    """
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "MIND_PALACE_RUNTIME_PASSWORD" in compose
    assert "MIND_PALACE_CURSOR_SECRET" in compose


# --------------------------------------------------------------------------
# 4. A bare `mindpalace` says what to do next.
# --------------------------------------------------------------------------


def test_a_bare_invocation_prints_a_getting_started_guide():
    """The empty invocation is the first run. It should not be an error.

    It used to print "Missing command." -- which is a statement about the
    person's typing -- and nothing else. The commands a newcomer needs are
    known, so the empty invocation is where they belong.
    """
    result = _invoke([])

    assert result.exit_code == 0, result.output
    output = result.output
    assert "Missing command" not in output
    assert "mindpalace init" in output
    assert "mindpalace remember" in output
    assert "mindpalace recall" in output


def test_a_bare_invocation_mentions_where_the_full_list_is():
    """Three commands are a start, not the whole product."""
    result = _invoke([])

    assert "mindpalace --help" in result.output


def test_the_guide_does_not_replace_the_help_page():
    """`--help` is still the complete list. The guide is the front door."""
    result = _invoke(["--help"])

    assert result.exit_code == 0
    assert "ingest-repo" in result.output
    assert "receipt" in result.output


# --------------------------------------------------------------------------
# The shipped entry point, not just the in-process one.
# --------------------------------------------------------------------------


def test_the_installed_entry_point_prints_the_guide():
    """`python -m cli.main` is what a developer without a console script runs.

    The in-process test above would pass even if the module entry point were
    broken, and the module entry point is what the wheel's `mindpalace`
    script is a thin wrapper around.
    """
    done = subprocess.run(
        [sys.executable, "-m", "cli.main"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
        env={**os.environ, "MIND_PALACE_RUNTIME": "0"},
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "mindpalace init" in done.stdout


@pytest.mark.parametrize("command", ["recall", "ingest-repo"])
def test_a_refused_connection_is_clean_in_a_real_process(command, tmp_path):
    """The same guarantee, in a process rather than in-process.

    `CliRunner` catches the exception and shows the output; a real process
    shows the traceback that `CliRunner` would have hidden. This is the test
    that would have caught the original defect.
    """
    args = [command]
    extra = {}
    if command == "ingest-repo":
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("# A\n\nThe datastore is PostgreSQL.\n", encoding="utf-8")
        args += [str(docs), "--corpus", "unreachable-db"]
        extra["MIND_PALACE_INGEST_ROOTS"] = str(tmp_path)
    else:
        args += ["what is the datastore"]

    done = _run_cli(args, DEAD_DATABASE_URL, extra)

    combined = done.stdout + done.stderr
    assert done.returncode != 0
    assert "Traceback (most recent call last)" not in combined, combined
    assert "asyncpg" not in combined, combined
    assert "database" in combined.lower()
