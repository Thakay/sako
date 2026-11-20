---
name: sako
description: Carry out project tasks with SAKO when the main checkout has a `.sako/` folder or the project instructions select it, or when the owner asks to adopt it. Gives task ownership, checks, evidence, and recovery.
---

# Work with SAKO

Before a project task, read `.sako/SAKO.md` from the main checkout down to its
**Read when needed** table and follow its daily loop; open a later section only
when its situation applies. Questions and discussion need no task record until
they change a file; `add` and `claim` come before that change. Change a row's
scope, prerequisites, done when or status with `edit`. Project instructions and
the owner's choices come first.

If another session handed you a task row as your brief, you are its worker. Run no
SAKO command, since that session holds the claim and closes the task.

If `.sako/` is absent and the owner asks to adopt SAKO, run `uvx sako init` in the
project (it needs uv), then read the installed `.sako/SAKO.md`.
