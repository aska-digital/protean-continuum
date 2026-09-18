---
goal: write the synthetic contract fixture for the audience filter tests
team6_agent: azaraki
from: lugia
---

## GOAL

This file is the backing artefact for fixture F1 (`delegated_brief_file`). The first user
message of the F1 session is byte-identical to this file, so audience rule C1 matches by
sha256(normalize(text)) and the session classifies as DELEGATED with reason C1.
