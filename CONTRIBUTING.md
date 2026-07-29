# Contributing

Thanks for considering a contribution.

## Setup

```bash
git clone https://github.com/sfaisal/agent-ready
cd agent-ready
pip install -e ".[dev]"
pytest
```

## Adding a rubric category

This is the most valuable kind of contribution, and the bar is deliberately
higher than for a bug fix. A new category should come with:

1. **A rationale for agent-specific failure.** Not "this is bad API design" —
   why does this specifically break an AI agent in a way it wouldn't break a
   human developer? If a human hits the same wall and shrugs, it isn't a
   category.
2. **A citation or a reproducible example.** Published research, official MCP
   documentation, or a demonstrable case where an agent picks the wrong tool
   or malformed arguments because of this gap.
3. **Tests**, including at least one case that should *not* trigger it. False
   positives erode trust in the tool faster than missed findings do.
4. **A weight proposal**, with reasoning. Weights currently sum to 1.0 and are
   judgement calls; adding a category means rebalancing.

## Adding a check to an existing category

Same as above, minus the weight discussion. Keep finding messages specific and
actionable — say what's wrong *and* why it matters to an agent. Compare:

- Bad: `"Missing description"`
- Good: `"3/3 parameters have no description (date_from, date_to, location) — the agent must guess their meaning."`

## Style

- `ruff check src tests` must pass
- Tests must pass on Python 3.10–3.12
- Prefer clarity over cleverness in rubric code; people read it to understand
  the rubric, not just to run it
