---
description: PAGASA warning ingest and CAP parsing
paths:
  - "backend/src/tidehold/warnings/**"
---

# Warnings

- Before deciding whether the official source carries a field, inspect the raw envelope the recorder stored, not the ingested tables: `warnings/` keeps only some parameters.
- When reading CAP, handle `status`, `msgType` and `references`; any dedupe or grouping key for alerts includes `replayed`.
