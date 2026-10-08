# SPDX-License-Identifier: Apache-2.0
"""Entry point for the local runtime: `python -m mindpalace_runtime`.

A module rather than a console script because the runtime is started by the CLI
spawning `sys.executable -m`, which guarantees it runs the *same* interpreter and
the *same* installed Mind Palace as the command that started it. A console script
would depend on PATH, and `pip install --user` plus a virtualenv is a good way to
end up with two versions of the product and a socket served by the wrong one.
"""

from __future__ import annotations

import asyncio
import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help"}:
        print(__doc__.strip())
        return 0

    # The runtime is started in the background by other commands, so anything it
    # prints is either swallowed or lands in someone's terminal uninvited. A model
    # loading progress bar in the middle of a `recall` is noise, not information.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    from api.runtime import RuntimeError_, serve

    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:
        return 0
    except RuntimeError_ as exc:
        # The only thing that prints here. Everything else the runtime does, it does
        # through the product's own error translation.
        print(f"mindpalace runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
