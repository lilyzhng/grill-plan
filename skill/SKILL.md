---
name: grill-plan
description: Grill any markdown doc (plan, design doc, spec) with sharp first-principle questions in a Google-Docs-style threaded UI. The agent generates questions anchored to exact passages, launches the grill-plan server, watches the threads file for the human's answers, and replies in-thread until every thread resolves.
---

# grill-plan skill

Given an input markdown file, interview the human relentlessly about every decision in it until
you reach shared understanding — with the discussion happening in a browser UI, threaded and
anchored to the doc, instead of a wall of terminal text.

## Invocation

`/grill-plan <filepath>` — any markdown doc: an execution plan, a design doc, a spec.

## Steps

1. **Read the entire doc.** Also read surrounding codebase/context files (CLAUDE.md, existing
   code). Never ask what you could find by reading.

2. **Identify every decision** — implicit and explicit. Each is a branch of the design tree.
   Prioritize: foundations (who/what/why) before implementation details; a changed foundation
   invalidates downstream branches.

3. **Write the grill questions.** For each of the top N branches (start with 5–8):
   - One question, one decision.
   - Anchor it to an EXACT phrase copied from the doc (anchor matching is whitespace-tolerant,
     but the words must match).
   - Lead with the strongest challenge, include your recommended answer when you have one.
   - Label them "Grill 1" … "Grill N".

4. **Seed the threads file** (schema in the repo README): one thread per question, first comment
   author = your agent name.

5. **Launch the server** (outside any command sandbox — it binds a localhost socket):
   ```bash
   python3 grill_plan.py <doc.md> --threads <session>.threads.json --port 7788 --no-open
   ```
   Open `http://127.0.0.1:7788` for the human (`?author=<name>` sets their display name,
   `?agent=<regex>` controls which authors render as agent-side).

6. **Watch for answers.** Poll or watch the threads file (mtime). When the human replies:
   - Read the thread. Judge the answer honestly — verdict first, then reasoning.
   - If the answer resolves the decision: reply with the verdict and set the thread's
     `status: "resolved"` (POST `/api/resolve` or edit the file).
   - If not: push back with ONE sharper follow-up in the same thread.
   - Append your reply to the thread's `comments` (POST `/api/comment` or edit the file —
     the UI updates live either way).

7. **Converge.** When all threads resolve (or the human says stop): summarize decisions made,
   write resolved decisions back into the plan (or a `_grilled.md` copy), and list any
   overridden recommendations as risks.

## Grilling rules

- **One question per thread.** Humans don't have context windows for batched questions.
- **Grill the implicit decisions** — technology defaults, orderings, exclusions, unstated
  assumptions. That's where plans die.
- **Have a position.** Recommend an answer; don't just interrogate.
- **A weaker but honest verdict beats a stronger sloppy one.** If the human's answer is good,
  say so and resolve — don't manufacture disagreement.
