# Move the task authority to a tracker

The one-time move of a project's [task authority](glossary.md) from SAKO's
ledger to a tracker. If you keep SAKO afterwards, work enters through
[task intake](../SAKO.md#task-intake).

## When to move

Move when several people or agents work across clones or machines, or you want a
scheduler to assign work. SAKO's lock and claims cover one checkout and its
worktrees; other clones and machines share no lock and no live roster, so shared
records allow one writer at a time. Move once; do not run two task systems.

## The move, for any tracker

1. **Settle claimed work.** Close finished tasks with evidence; park the rest by
   hand (Status `parked: <reason>`). Commit, then fix what
   `check --gate --session <id>` names.
2. **Create one item per row left in `TASKS.md`.** The Task text becomes the title
   and Done when becomes the acceptance (a Beads field; elsewhere, the body). Scope,
   Source, the row's ID and any handoff notes go in the body. A parked row becomes a
   parked item with its reason. Map Pri with this table, then set the row's Status to
   `parked: moved to <item id>`: it keeps its ID, cannot be claimed, and records the
   mapping.

   | SAKO | Beads | GitHub label | Linear |
   |---|---|---|---|
   | P1 | 1 | P1 | 2 (High) |
   | P2 | 2 | P2 | 3 (Medium) |
   | P3 | 3 | P3 | 4 (Low) |

3. **Link prerequisites in a second pass**, once every item has an id: each ID in
   After becomes a blocked-by link.
4. **Keep `DONE.md` as the history.** It is plain Markdown, but not in Git, and
   `git clean -x` deletes it: keep a copy.
5. **Choose how work runs from now on.**
   - **Keep SAKO for execution.** The tracker is the authority for intent and
     status; a local row holds only ownership, scope and evidence. Take in each item
     you start with its tracker id as `--source`:

     ```sh
     python3 .sako/sako.py add "Print sorted names" --done-when "The agreed sample matches" --source demo-r2k --pri P2 --scope src/
     ```

     Claim the item in the tracker, then `claim` the row. After `close`, return the
     receipt as a tracker comment or closing note. SAKO never reads or writes the
     tracker: nothing syncs, and the gate checks only local rows.
   - **Remove SAKO.** Run `python3 .sako/sako.py remove` in the main checkout. It
     keeps `.sako/config.json` and `.sako/work/`; keep that folder as an archive or
     delete `.sako/`. See [update and removal](update-and-remove.md).

## Beads

- The project moved: its old GitHub address redirects to
  github.com/gastownhall/beads, with documentation at beads.gascity.com.
- `bd init` edits `.gitignore` and makes its own commit, without your project's
  trailers. Without the skip flags it also writes `AGENTS.md`, client setup and Git
  hooks.
- Anonymous metrics stay on until `bd metrics off`.

```sh
brew install beads    # or: npm install -g @beads/bd
bd init --prefix demo --non-interactive --skip-agents --skip-hooks
bd metrics off
bd create "Print sorted names" -p 2 -t task -l sako -d "Scope: src/. Source: PLAN.md#N2" \
  --acceptance "The agreed sample matches" --external-ref T-12 --silent
bd dep add demo-r2k demo-q9c    # demo-r2k waits for demo-q9c
bd ready --json
BEADS_ACTOR=<actor> bd update demo-r2k --claim
bd update demo-r2k --status deferred --notes "parked: <reason>"
bd close demo-r2k --reason "<receipt>"
```

`--silent` prints the new id. A claim sets the assignee and `in_progress`; a second
actor's claim fails with exit 1. Claims hold a lease: `bd heartbeat` renews it and
`bd reclaim` reopens stale ones. Closing another actor's item needs `--force`.

Bulk: `bd import tasks.jsonl --dry-run`, then without `--dry-run`. Each line is one
JSON object; only `title` is required, and supplied ids are kept. Tested fields:

```json
{"id":"demo-q9c","title":"Discover note names","priority":1,"labels":["sako"],"acceptance_criteria":"Filtering is checked","external_ref":"T-11"}
{"id":"demo-r2k","title":"Print sorted names","priority":2,"labels":["sako"],"acceptance_criteria":"The agreed sample matches","external_ref":"T-12","dependencies":[{"issue_id":"demo-r2k","depends_on_id":"demo-q9c","type":"blocks"}]}
```

`--source` takes the id, such as `demo-q9c`; child items look like `demo-q9c.1`.

## GitHub Issues

- The dependency flags, `--parent` and `--type` need gh 2.94.0 or later (2026-06-10).
- There is no official bulk import: loop over `gh issue create`.
- Claiming by assignee is not atomic: check the assignees first.
- Sub-issues show hierarchy, not order; use blocked-by links for After.

Sign in with `gh auth login` or a `GH_TOKEN` with `repo`, `read:org` and `gist`
scopes. With no built-in priority, P1 to P3 are labels, which must exist first:
repeat the first line for P2, P3 and `sako`.

```sh
gh label create P1 --color B60205 --description "SAKO priority 1" --force
gh issue create -R OWNER/REPO --title "Print sorted names" --body-file body.md --label P2 --label sako
gh issue edit 42 --add-blocked-by 41
# older gh: gh api -X POST repos/OWNER/REPO/issues/42/dependencies/blocked_by -F issue_id=<internal id of 41>
gh issue edit 42 --add-assignee @me
gh issue close 42 --reason completed --comment "<receipt>"
```

`create` prints the URL. An issue is only open or closed (completed, not planned,
duplicate), so park with a label and a comment. `--source` takes `OWNER/REPO#42`.

## Linear

- There is no official issue CLI: use the GraphQL API at
  `https://api.linear.app/graphql`, `@linear/sdk` or the MCP server.
- A personal API key goes in the header as `Authorization: <KEY>`, without `Bearer`.
- The CSV import drops relations, and there is no atomic claim.

Add the MCP server to Claude Code with
`claude mcp add --transport http linear-server https://mcp.linear.app/mcp`. Through
the API, look up ids with `teams { nodes { id key } }`, `issueLabels` and `viewer`,
then call these mutations. `issueCreate` returns the identifier and URL. The relation
reads "ENG-41 blocks ENG-42"; ids take a UUID or an identifier.

```text
issueCreate(input: {teamId: "TEAM_UUID", title: "Print sorted names", priority: 3,
  labelIds: ["LABEL_UUID"], description: "Done when: the agreed sample matches. Scope: src/. Source: PLAN.md#N2. Row: T-12"})
issueRelationCreate(input: {issueId: "ENG-41", relatedIssueId: "ENG-42", type: blocks})
```

Each team has its own states, by default Backlog, Todo, In Progress, Done and
Canceled. Open is Todo. To claim, check the assignee, then assign it and move it to
In Progress (`delegateId` hands it to an agent user). Park as Backlog with a
comment. To close, post the receipt with `commentCreate` and move the issue to Done.

Bulk, admins only: `npm i --location=global @linear/import`, then `linear-import`.
The CSV columns include Title, Description, Priority as a word, Status, Assignee and
Labels separated by ", ". Add relations through the API afterwards.

`--source` takes the identifier, such as `ENG-42`. It changes when the issue moves to
another team; old URLs redirect, and the API keeps `previousIdentifiers`.

## How this was checked

The tracker commands and limits were checked against each tool's official
documentation on 2026-09-22. The Beads commands were also run, with bd 1.3.0 in a
scratch repository. The GitHub and Linear commands were not run.

Sources: Beads at github.com/gastownhall/beads and beads.gascity.com (CLI reference:
init, create, update, dep, ready, close, import); GitHub at cli.github.com/manual
(gh_issue_create, gh_issue_edit, gh_issue_close), the GitHub changelog entries of
2025-08-21 (issue dependencies) and 2026-06-10 (dependencies in gh), and
docs.github.com/en/rest/issues/issue-dependencies; Linear at
linear.app/developers/graphql, linear.app/developers/sdk, linear.app/docs/mcp,
linear.app/docs/cli-importer and the schema in github.com/linear/linear.
