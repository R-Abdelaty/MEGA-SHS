# University Policy Knowledge Base

This directory is the policy/RAG layer for the Self-Healing University
Scheduler. Policy text belongs here rather than in the agent's system prompt.

## Structure

- `index.json` defines the supported categories, retrieval keywords, document
  paths, and policy status.
- One Markdown document stores each policy category.
- `deterministic_rules.json` stores confirmed machine-readable rules used by
  `check_validity`.

## Policy statuses

- `configured`: supplied project policy that the agent may apply.
- `configured_with_placeholders`: confirmed rules plus explicitly identified
  gaps.
- `placeholders_require_confirmation`: no detailed rule exists yet; the agent
  must request confirmation before relying on it.

Content marked `REQUIRES CONFIRMATION` is never authoritative permission to
guess.

## Updating policy

1. Update the relevant category document; do not put detailed policy back in the
   system prompt.
2. If the policy is deterministic and supported by `check_validity`, update
   `deterministic_rules.json` and the corresponding validator check together.
3. Add retrieval keywords to `index.json` only when they materially improve
   routing.
4. Run:

   `python -m unittest discover -s tests -v`

Do not add a new GUC/GIU policy unless it comes from the user or an authorized
university source.
