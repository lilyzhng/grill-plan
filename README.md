# grill-plan

**Let your AI agent grill your plan — Google-Docs-style — before it builds.**

Your agent reads any markdown doc (an execution plan, a design doc, a spec), generates sharp
first-principle questions, and pins each one to the exact sentence it challenges. You answer
inside comment threads, right next to the text. The agent replies in-thread, live. Discussion
converges → threads resolve → the plan is actually aligned before anyone executes.

No accounts, no cloud, no dependencies. One Python file, one HTML file, one JSON file.

```
┌─ TOC ──┬───────── your doc ─────────┬──── margin threads ────┐
│ 1. …   │  …conducting ⟨at least one │  ● Grill 1             │
│ 2. …   │  independent review per    │  Agent: What source    │
│ 3. …   │  workstream⟩ that does…----│  qualifies as          │
│        │                            │  independent here?     │
│        │                            │  You: [reply box]      │
└────────┴────────────────────────────┴────────────────────────┘
```

## How it works

```
agent writes questions ──► threads.json ──► browser UI (SSE, live)
        ▲                                        │
        └──── agent watches file ◄──── you reply in the thread
```

- `grill_plan.py` — zero-dependency Python server (stdlib only). Serves the UI, the doc, and a
  JSON API. Persists threads to a JSON file and **watches it for external edits**, pushing
  changes to the browser over SSE.
- `static/index.html` — single-page UI: rendered markdown (with images + mermaid), amber anchor
  highlights, dashed connectors, margin comment cards laid out next to their anchors
  (collision-resolved, Google-Docs style), TOC sidebar, numbered jump chips with progress,
  text-selection → new thread, and a raw-markdown edit mode.
- `threads.json` — the store **and** the agent bridge. Any process that can edit a JSON file can
  participate in the discussion. That's the whole integration surface.

## Quick start

```bash
python3 grill_plan.py path/to/your-plan.md
# opens http://127.0.0.1:7788
```

Reply as yourself in any thread (`?author=Lily` to set your name). The agent replies by
appending to `threads.json` — see [skill/SKILL.md](skill/SKILL.md) for the full agent loop,
including how a Claude Code session generates the questions, watches for your answers, and
responds in-thread.

Seed questions look like this:

```json
{
  "doc": "/abs/path/plan.md",
  "threads": [
    {
      "id": "t-grill1",
      "label": "Grill 1",
      "anchor": "exact phrase copied from the doc",
      "status": "open",
      "comments": [
        {"author": "Agent", "text": "Sharp question here.", "ts": "2026-07-10T00:00:00+00:00"}
      ]
    }
  ]
}
```

## The grill methodology

The UI is the surface; the method is the point:

1. **One question, one decision.** Every thread challenges exactly one branch of the design tree.
2. **Grill the implicit decisions**, not just the headline ones — the assumptions the doc takes
   for granted are where plans die.
3. **Anchored, not abstract.** Each question pins to the exact sentence it challenges, so the
   discussion happens in context.
4. **Resolve or it didn't happen.** A thread ends with a resolution written back into the plan,
   not with a vibe.

## API

| Route | Method | Body |
|---|---|---|
| `/api/doc` | GET | — (markdown + title) |
| `/api/doc` | POST | `{markdown}` (edit mode save) |
| `/api/threads` | GET | — (full store) |
| `/api/threads` | POST | `{anchor, author, text, label?}` |
| `/api/comment` | POST | `{thread_id, author, text}` |
| `/api/resolve` | POST | `{thread_id, status: open\|resolved}` |
| `/api/events` | GET | SSE — fires on any mutation, including external file edits |

Doc-relative assets (images, linked HTML) are served from the doc's directory, path-traversal guarded.

## Tests

```bash
python3 -m unittest tests.test_grill_plan -v
```

## License

MIT
