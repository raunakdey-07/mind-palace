# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The schema migrations, shipped as a package so a wheel can apply them.

Alembic loads `env.py` and the revision scripts **by path**, not by import, so
this `__init__.py` is never executed by a migration. It exists for exactly one
reason: `setuptools` only includes files that live inside an importable package,
and before this file existed the wheel shipped neither `migrations/` nor
`alembic.ini`.

That is not cosmetic. `pip install mindpalace-os` produced a 13 MB install from
which the documented third step --

    python -m alembic -c migrations/alembic.ini upgrade head

-- could not run at all, because there was no `migrations/` directory to point
at and no `alembic` to run it with. A newcomer following the README in a clean
container hit `No module named alembic`, and a newcomer following the
`pip install mindpalace-os` line hit `No such file or directory`.

`mindpalace init` now applies the schema itself when it can find this package,
so the bare wheel reaches a working first command. The README step still works
for a source checkout, and both are covered by the clean-room job.
"""

from __future__ import annotations

from pathlib import Path

#: The directory alembic should be pointed at. Resolved from this module rather
#: than from the working directory, because after `pip install` the working
#: directory is wherever the user happened to be.
HERE = Path(__file__).resolve().parent

ALEMBIC_INI = HERE / "alembic.ini"
