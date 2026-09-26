# Intake recipes

Worked examples of task intake from a spec checklist, a brief, an issue tracker and
a phase plan. The rules are in [Task intake](../SAKO.md#task-intake); these recipes
apply them and add no behavior.

Each recipe turns one selected planner item into one local task per action, with
`--source` naming the item. The planner stays authoritative: SAKO keeps the
reference as exact text and never fetches, watches or edits the source. Run the
same intake again whenever you revisit the planner; the method says how a repeated
or changed item is handled, and how to return the result.

## A spec checklist

For checkbox items in a SPEC.md or a README section.

- Give each item a short ID that survives edits, such as
  `- [ ] export-2: Export sorts names`, and use `SPEC.md#export-2` as the source.
  Line numbers and positions shift when the list changes.
- Take `--done-when` from the item's acceptance wording.
- Scope the files the work needs, plus `SPEC.md` when the task ticks the box.
- Tick the box before `verify` and `close`, and commit it with the work: a tick
  after close changes checked content and needs a fresh check. If the spec is the
  owner's, leave the box to them and report the receipt instead.

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
  and set its Status to `parked:` with the reason, so a fresh session waits too.

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
- SAKO never reads or writes the tracker. Return the result through the project's
  authorized procedure: a comment with the receipt, or a closing reference such as
  `Fixes owner/repo#42` in the commit that finishes the last action. GitHub, not
  SAKO, closes the issue when that commit is merged into the default branch.

```sh
python3 .sako/sako.py add "Import skips blank lines" --done-when "A notes file with blank lines imports without an error" --source owner/repo#42/1 --scope src/
```

## A phase plan

For a PLAN.md with phases and numbered steps.

- Reference each step by phase and number, such as `PLAN.md#phase-2/step-3`. Keep
  a step's number once it is taken: renumbering points old references at
  different work.
- Add the selected steps in plan order, so each prerequisite already has an ID,
  and pass it with `--after` (in the example, T-1 is step 1). `next` holds the step and
  `claim` refuses it until the prerequisite is done.
- Pass the plan's priority with `--pri`; `next` ranks ready steps by it.
- When the plan changes, rerun the intake; a changed step refuses. Reconcile
  deliberately: edit the row to match when it is the same work and not yet
  started; give genuinely new work a new reference, such as a new step number;
  set a dropped step's Status to `parked: <reason>`.

```sh
python3 .sako/sako.py add "Document the export command" --done-when "The README shows the export command and its sample output" --source PLAN.md#phase-2/step-3 --after T-1 --pri P2 --scope README.md
```
