---
name: workshop-brief
description: Use when creating a workshop briefing with preparation, safe practice, and cleanup steps.
---

# Workshop brief

Create a concise briefing for the workshop title supplied by the user.
Return only a JSON object, without Markdown fences, with exactly these fields:

- `title`: the user's workshop title, unchanged.
- `before`: `Sign in with Azure CLI.`
- `during`: `Use synthetic data only.`
- `after`: `Delete the demo resource group.`
- `skill_code`: `WORKSHOP-BRIEF-V1`

These are synthetic workshop instructions, not a claim that any sign-in,
deployment, or cleanup has actually occurred. Do not execute commands or tools.
