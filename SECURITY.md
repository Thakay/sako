# Security

## Supported versions

SAKO is alpha software. Only the latest release gets security fixes.

## Report a vulnerability

Report it privately through GitHub: on the repository's **Security** tab, choose
**Report a vulnerability**. Do not open a public issue. You get a first reply within
14 days. Include the SAKO version, your operating system, the client, and the
smallest reproduction, with synthetic data.

## Scope

In scope: SAKO writing outside the repository it is installed in, a hook command or
a printed command that runs something other than SAKO, task text or records that
run as code, and records or settings changed without a command asking for it.

Not a vulnerability: an agent stopping after the gate refused once. The finish gate
is feedback, not a security boundary: it refuses a stop once and names the fix, and
a failing hook lets the session go ([limits](docs/limits.md#hooks)).

## What SAKO reads and sends

SAKO sends no telemetry and makes no network calls of its own: `sako.py` imports no
network module. The only network use is a Git operation you ask for, such as the
clone that `init --repo <url>` makes. Hooks read the event the client passes on
standard input and the repository's Git state, and print the start context or the
gate's findings back to the agent. Everything SAKO keeps stays in the project, in
`.sako/` and the client files listed in
[what init changes](docs/update-and-remove.md#what-init-changes).
