---
name: sako
description: Carry out project tasks with SAKO when the main checkout has a `.sako/` folder or the project instructions select it, or when the owner asks to adopt it. Gives task ownership, checks, evidence, and recovery.
---

# Work with SAKO

Before a project task, read `.sako/SAKO.md` from the main checkout and follow its
daily loop; read its other sections when their opening line applies. Questions
and discussion need no task record. Project instructions and the owner's choices
come first.

Pass the session ID from the start context to every command; in Claude Code and
Codex a command without `--session` uses the client's session ID. If no start
context appeared, check this existing session in with
`python3 .sako/sako.py start --session <id>` (`python` on Windows), using an ID
unique to this conversation. This registers the session; it does not launch an
agent. After checking in yourself, run `check --gate` and `end` before you stop.

If `.sako/` is absent and the owner asks to adopt SAKO, follow **Install, update
and remove** in the SAKO.md of the package or checkout they supplied.
