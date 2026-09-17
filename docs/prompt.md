# Original development brief

> Historical record. This is the brief the project was started from, kept as written. Two
> things have changed since: the scoping notes now live under English file names, and the
> code, CLI and configuration are in English (only the digest is French). See the
> [implementation journal](implementation-journal.md).

You will develop V1 of the project "veille-by-jev": a Python CLI that produces a French
Markdown digest every night from the day's Hacker News articles. Answer me in French.

Scoping documentation, to be read in this order before writing a single line of code:

1. `README.md`
2. `docs/pipeline-workflow.md`
3. `docs/typesafe-triage.md`
4. `docs/developer-handoff.md`

Those notes are the reference. Do not modify them: they are documents, not code.

Environment: Linux, Python 3.11 or later, environment and dependencies managed with `uv`.

Working rules, mandatory:

- Work step by step, in the order of the "Developer handoff" note, section "Suggested working
  order".
- After EACH step, stop: show me the files created and the real output of the command, then
  wait for my validation. Do not chain steps on your own initiative.
- No invented data: if a command fails, show the exact error rather than a plausible result.
- No API key in the repository: environment variables, plus a `.env.example` without values.
- Before adding a dependency that is not listed in the handoff, ask me.

Start with step 1 only: the `vbj collect` command, until `data/<date>/items.json` is correct
for a real Hacker News day. No model call at that step; I will not have an API key to give you
before step 3.

When step 1 is finished, show me:

- the file tree created;
- the first lines of `items.json`;
- the number of items collected and the date range covered;
- the exact command I should run to reproduce it.
