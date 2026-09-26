# One optional SAKO skill

`sako/SKILL.md` is the entry for an agent using SAKO in a project. It sends the
agent to the installed `.sako/SAKO.md` for task intake and execution, follows the
project's selected planner, or the method's **No planner? Start here** when none is
selected, and keeps daily use independent of this checkout. Project instructions
can select another planner without another skill.

Every `init` installs the skill under `.agents/skills/sako/`, where Codex, Cursor and
other clients look for skills; when it wires Claude Code, `init` also installs a copy
under `.claude/skills/sako/`. The skill needs no hooks: with `--client none` the
agent runs the session commands itself. See
[client compatibility](../docs/compatibility.md).
