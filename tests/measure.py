#!/usr/bin/env python3
"""Report fixture overhead, not user adoption time. Run from the source checkout."""
from pathlib import Path
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time

import selftest as fixture


def main():
    with tempfile.TemporaryDirectory(prefix="sako-measure-") as workspace:
        root = Path(workspace)
        blank = root / "blank"
        blank.mkdir()
        fixture.git(blank, "init", "-q", "-b", "main")
        before = time.perf_counter()
        fixture.succeeds(fixture.seed(blank))
        install_ms = (time.perf_counter() - before) * 1000
        package = [p for p in blank.rglob("*") if p.is_file() and not {".git", "state"} & set(p.parts)]
        footprint = {"files": len(package), "bytes": sum(p.stat().st_size for p in package),
                     "method_words": len((blank / ".sako/SAKO.md").read_text().split())}
        fixture.succeeds(fixture.seed(blank, "--client", "codex", "--client", "claude"))
        package = [p for p in blank.rglob("*") if p.is_file() and not {".git", "state"} & set(p.parts)]
        integrated = {"files": len(package), "bytes": sum(p.stat().st_size for p in package)}
        clients = {}
        for agent in ("codex", "claude"):
            target = root / agent
            target.mkdir()
            fixture.git(target, "init", "-q", "-b", "main")
            fixture.succeeds(fixture.seed(target, "--client", agent))
            package = [p for p in target.rglob("*") if p.is_file() and not {".git", "state"} & set(p.parts)]
            clients[agent] = {"files": len(package), "bytes": sum(p.stat().st_size for p in package)}
        project = fixture.make_repo(root / "project")
        fixture.succeeds(fixture.seed(project, "--client", "none"))
        fixture.write(project, {f"src/data-{n}.txt": "x" * 4096 for n in range(100)})
        fixture.write(project, {".sako/config.json": json.dumps({
            "verify_command": [sys.executable, "-c", "from pathlib import Path; assert Path('src/app.py').read_text() == 'print(1)\\n'"],
            "verify_paths": ["src"]})})
        fixture.commit_all(project, "measurement fixture")
        pid = fixture.fake_agent()
        samples = {}
        commands = {"start": ["start", "--session", "measurement-session"],
                    "check": ["check"], "next": ["next"], "verify": ["verify"],
                    "gate": ["check", "--gate", "--session", "measurement-session"]}
        for name, args in commands.items():
            samples[name] = []
            for _ in range(7):
                started = time.perf_counter()
                fixture.succeeds(fixture.installed(project, *args, pid=pid))
                samples[name].append((time.perf_counter() - started) * 1000)
        fixture.kill(pid)
        output = {"python": platform.python_version(), "platform": platform.system(),
                  "git": subprocess.check_output(["git", "--version"], text=True).strip(),
                  "no_signal_install": footprint, "no_signal_install_ms_single_sample": round(install_ms, 1),
                  "single_client_installs": clients,
                  "both_clients": integrated,
                  "runtime_lines": len(fixture.SAKO.read_text().splitlines()),
                  "covered_files": 101, "covered_bytes": 409609,
                  "trials_per_command": 7,
                  "command_median_ms": {name: round(statistics.median(values), 1) for name, values in samples.items()}}
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
