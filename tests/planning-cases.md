# Planning trials

These cases exercise judgment in the installed skill and the method's default
planner. They are manual trials, not executable tests or completed adopter
evidence. Use disposable projects. Give a trial agent the project, installed SAKO,
and only the request and raw inputs. Keep the evaluation criteria separate from its
prompt. Supply answers only when asked; record the questions, resulting files, and
observations.

| Case | Request and raw inputs | Owner answer if needed |
|---|---|---|
| Idea | "Use SAKO to help me start a tool for sorting my notes." Empty Git repo; no brief or tests | First useful outcome is a local alphabetical preview; preserve original files |
| Partial structure | "Use SAKO to continue this export tool." Code exports a local file; old TODO lists accounts and sync; a current brief says keep everything local | Current brief governs; first fix the export's handling of empty input |
| Unstructured code | "I have been building this without much planning. Help me take the next step with SAKO." A listing script prints zeta then alpha; scratch notes mention possible remote sync | Want alphabetical local output; keep both entries; remote work is out of scope |
| Queue exhausted | "What next?" Goal promises both valid-input behavior and a clear empty-input error; only the first has completed evidence | The original goal still stands |
| All work held | "Continue." One parked decision needs an output format; the only implementation row is blocked by it | Decision is not available yet |
| Invalidated plan | "Keep this local; I no longer want accounts." Account work is open and referenced by a later task | Retain only local export behavior |
| Goal complete | "What next?" Agreed goal has passing behavioral evidence, no unfinished work, and several optional future ideas | No additional outcome chosen |
| Another planner | "Use our planner and SAKO for execution." Existing planner instructions, authoritative brief, and selected work with acceptance and scope | Existing intent source remains authoritative |
| Authorized batch | "Implement the three cases already agreed in our brief." The brief gives three bounded behaviors and test expectations | No further approval is needed within that scope |
| Optional first trial | "Let me try the first local preview before adding export." A brief already defines both behaviors | Pause after the preview and wait for trial feedback before export |

Evaluate whether the agent:

- Separates observations, current intent, and assumptions; asks only decisions
  that matter and stops exploration once it can act.
- Prepares bounded work with observable completion, or a bounded investigation
  when implementation would be premature.
- Preserves useful conventions and authority without introducing a second brief,
  full backlog, mandatory PRD, phases, or an unrelated skill installation.
- Recognizes held, invalidated, and completed work instead of filling the queue
  indiscriminately or proceeding past a missing consequential answer.
- States missing/failing automated checks honestly, keeps configured verification
  requirements, and records the actual result of a manual or discovery task.
- Leaves durable context that a new session can interpret without chat history.
- Uses product sketches, task batches, and first trials only when they help or the
  owner requests them; respects a chosen trial boundary without imposing one on
  already authorized work.

For the final pickup, provide a different session only the resulting project
and "Continue using SAKO." Observe the action it actually chooses. Do not infer
successful judgment from the mere existence of a task, guide, or context file.
Timing, number of interventions, and perceived overwhelm need outside builders;
a trial run by the author cannot establish those user outcomes.
