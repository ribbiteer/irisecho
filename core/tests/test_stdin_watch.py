# SPDX-License-Identifier: AGPL-3.0-or-later
"""The desktop shell's lifeline pipe must not reach the programs the core starts."""

import subprocess
import sys
import textwrap

# What `serve --stdin-watch` does, then start a program the way engines are
# started: output captured, stdin left alone.
CORE = textwrap.dedent(
    """
    import subprocess, sys, time
    from irisecho_core.cli import watch_stdin

    watch_stdin()
    time.sleep(0.3)  # let the watcher block on the pipe
    child = subprocess.Popen([sys.executable, "-c", "print('started')"], stdout=subprocess.PIPE)
    try:
        out, _ = child.communicate(timeout=20)
        print(out.decode().strip(), flush=True)
    except subprocess.TimeoutExpired:
        child.kill()
        print("hung", flush=True)
    time.sleep(30)  # still here when the pipe closes: the watcher must end us
    """
)


def test_children_start_while_the_lifeline_is_watched():
    core = subprocess.Popen(
        [sys.executable, "-c", CORE], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        assert core.stdout.readline().decode().strip() == "started"
        core.stdin.close()  # the shell goes away
        assert core.wait(timeout=10) == 0
    finally:
        core.kill()
        core.wait()
