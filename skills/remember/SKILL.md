---
name: remember
description: Write a note about a move in this project's procedural memory. Use at the end of a piece of work, when a step went a way the counts alone will not explain to the next run.
---

# Remember

The memory already counts what happened: how often a move was made, how often it
landed, what it tends to fail as. Counts cannot say *why*. The `remember` tool
attaches one sentence to one move so the next run reads the reason along with
the number.

## When a note is worth writing

Write one when the work is done and you know something the counts do not:

- A move failed for a reason that will repeat — an ordering constraint, a
  prerequisite, a flag that has to be set first.
- A move that looks wrong is in fact right here, and the statistics will keep
  arguing against it.
- The obvious alternative was tried and is worse, and nothing in the graph
  records that it was tried at all.

## When a note is not worth writing

- The counts already say it. "This command usually fails" is what support
  counts are for.
- It is true of this session only — a typo, a flaky service, a one-off.
- It restates the tool's own documentation, or names a file, a path or a
  secret. A note is about the *move*, and it is read in other sessions.

## How

Ask `inspect` for the move's `edge` key, then call `remember` with that key and
the note. One note, one move, one sentence; the text is capped at 500
characters and anything credential-shaped is refused.

At most one note per piece of work. The nudge arrives once, when the work ends;
a note after every step is noise the next run has to read past.
