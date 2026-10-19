# Intake recipes

Worked examples of task intake from common sources, and of a row for an act only
one session may do at a time. The rules are in [Task intake](../SAKO.md#task-intake);
these recipes apply them and add no behavior.

Each recipe turns one selected planner item into one local task per action, with
`--source` naming the item. The planner stays authoritative, and SAKO never
fetches, watches or edits the source. Rerun the intake whenever you revisit the
planner; the method covers repeated and changed items and returning the result.

`add` prints the task's ID first, as in `T-4: open`. Take each ID from there,
never from a predicted number, since another session can add a task between your
commands. [A phase plan](#a-phase-plan) shows a one-line capture.

## A spec checklist

For checkbox items in a SPEC.md or a README section.

- Give each item a short ID that survives edits, such as
  `- [ ] export-2: Export sorts names`, and use `SPEC.md#export-2` as the source.
  Line numbers and positions shift when the list changes.
- Take `--done-when` from the item's acceptance wording.
- Scope the files the work needs, plus `SPEC.md` when the task ticks the box.
- Tick the box before `verify` and `close`, and commit it with the work. A tick or
  other edit after close is new work, with its own task and a fresh check. If the
  spec is the owner's, leave the box to them and report the receipt instead.

```sh
python3 .sako/sako.py add "Sort exported notes by name" --done-when "Exporting the three-note sample prints Apple, Mango, Pear in that order" --source SPEC.md#export-2 --scope src/ --scope SPEC.md
```

## A brief with several agreed behaviors

For a README or BRIEF.md that lists agreed behaviors in plain sentences.

- Give each behavior an anchor that survives edits, such as its number
  (`BRIEF.md#2`) or a short name, and use it as the source.
- Make one task per behavior you can finish and check on its own; behaviors that
  share one change can be one task whose done when names each of them.
- When the owner wants to try one behavior before the next starts, add the next one
  and park it with the reason (`edit T-n --park REASON`), so a fresh session waits
  too.

```sh
python3 .sako/sako.py add "An empty name gets a clear error" --done-when "python3 greet.py with no name prints Usage: greet.py NAME and exits 2" --source BRIEF.md#2 --scope greet.py
```

## An issue-tracker item

For an issue such as `owner/repo#42` on GitHub or `ENG-42` in Linear.

- Use the tracker's full ID as the source.
- Take `--done-when` from the issue's acceptance text. If it has none, ask the
  owner instead of inventing one.
- When one issue needs several local actions, give each its own reference, such
  as `owner/repo#42/1` and `owner/repo#42/2`. A second action under the same
  reference refuses.
- Return the result through the project's authorized procedure: a comment with
  the receipt, or a closing reference such as `Fixes owner/repo#42` in the commit
  that finishes the last action. GitHub, not SAKO, closes the issue when that
  commit is merged into the default branch.

```sh
python3 .sako/sako.py add "Import skips blank lines" --done-when "A notes file with blank lines imports without an error" --source owner/repo#42/1 --scope src/
```

## A phase plan

For a PLAN.md with phases and numbered steps.

- Reference each step by phase and number, such as `PLAN.md#phase-2/step-3`. Keep
  a step's number once it is taken. Renumbering points old references at
  different work.
- Add the selected steps in plan order, so each prerequisite already has an ID,
  and pass it with `--after`; the example takes step 1's ID from `add`'s output.
  `next` holds the step and `claim` refuses it until the prerequisite is done.
- A step that is the owner's own act or decision gets a row with its source too,
  parked with the reason, as in `edit T-n --park "owner: approves the format"`.
  Park a step you defer the same way. Once the owner has acted, open it with
  `edit T-n --open`, then claim and close it.
- Pass the plan's priority with `--pri`; `next` ranks ready steps by it.
- A document that tracks the plan's progress names rows by ID and never copies
  their status.
- When the plan changes, rerun the intake; a changed step refuses. If it is the
  same work and nobody has started it, update its row with `edit`, or by hand for
  the task text. Give genuinely new work a new reference, such as a new step
  number, and park a dropped step with its reason.

```sh
step1=$(python3 .sako/sako.py add "Add the export command" --done-when "python3 notes.py export prints the three sample notes" --source PLAN.md#phase-2/step-1 --scope src/ | cut -d: -f1)
python3 .sako/sako.py add "Document the export command" --done-when "The README shows the export command and its sample output" --source PLAN.md#phase-2/step-3 --after "$step1" --pri P2 --scope README.md
```

## A review's findings

For findings a reviewer returns on work that waits for the verdict, such as a merge.

- Add one task per finding you will fix, with the finding as its source, such as
  `REVIEW.md#F3`. Its done when names what the fixing session can observe, never
  the reviewer's verdict.
- When the round starts, add the row that waits for the verdict, such as the
  merge, with `--after` on each fix. Its close evidence cites the reviewer's
  passing re-check.
- A finding the re-check sends back is new work with its own source, such as
  `REVIEW.md#F3/r2` for round 2. List it in the waiting row's After with
  `edit T-n --after`, which replaces the list, so name every fix.

```sh
fix=$(python3 .sako/sako.py add "Export keeps the note order stable" --done-when "Two exports of the sample print the same order" --source REVIEW.md#F3 --scope src/ | cut -d: -f1)
python3 .sako/sako.py add "Merge the review fixes" --done-when "The fixes are on main and the reviewer's re-check passes" --source REVIEW.md#merge --after "$fix" --scope src/
```

## A row from another SAKO ledger

For work found in one project that belongs to another, each with its own kit.

- Record it when you find it, with `add` run from the checkout of the project it
  changes, since SAKO works on the repository of the folder it runs in.
- Name the sending row in `--source` by its records, a commit that holds it when
  the records are in Git, its file and its ID, such as
  `ops-records@3f2c1a9:work/TASKS.md#T-12`.
- Park the sending row in its own ledger with the new ID, such as
  `edit T-12 --park "moved to app T-7"`.
- When a kit serves a bounded effort, such as a demo in its own clone, move its
  open rows before its records are frozen.

```sh
python3 .sako/sako.py add "Export skips hidden notes" --done-when "Exporting a folder with a hidden note leaves it out" --source ops-records@3f2c1a9:work/TASKS.md#T-12 --scope src/
```

## An act only one session may do at a time

For an act on a shared thing that is not a file, such as landing work on `main` or
restarting a demo server.

- Give the act its own row, which does nothing else, scoped by a path no file uses,
  such as `@land/main` or `@server/8080`, and claim it when the act starts. While
  it is claimed, no other session can claim a row scoped to that path or to `.`,
  which overlaps every path. The path covers no file, so it only locks the act
  against other claims.
- A session that only uses the server claims nothing. SAKO does not schedule, so
  agreeing on a safe moment with the sessions that use it stays a message.

```sh
python3 .sako/sako.py add "Restart the demo server on the new build" --done-when "The server on port 8080 answers with the new version" --scope @server/8080
```
