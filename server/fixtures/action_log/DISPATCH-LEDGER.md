# DISPATCH-LEDGER — dispatches (FROZEN fixture for action-log tests)

## Zero-context era

| task id | owner profile | model (verified from config.yaml) | domain | allowed decisions | forbidden decisions | owned files | upstream artifact | launch session id | exit artifact |
|---|---|---|---|---|---|---|---|---|---|
| fixture-dispatch-01 | mozi | nous / fixture-model | implementation | script layout, flags, exit codes | architecture changes; GitHub writes | `fixtures/action_log/dispatch-one.md` | none (fixture) | proc_fixture_0001 | same file, with commands run |
| fixture-dispatch-02 | shaka | nous / fixture-model | verification | gate verdicts, evidence checks | implementation; config edits | `fixtures/action_log/dispatch-two.md` | fixture brief on disk | proc_fixture_0002 | same file, with read-backs |

## Current era

| task id | owner profile | model (verified from config.yaml) | domain | allowed decisions | forbidden decisions | owned files | upstream artifact | evidence | verification | loop state | next action | notes | lane | token cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fixture-dispatch-03 | mozi | nous / fixture-model | implementation | code layout, test choices | architecture; deploys | `fixtures/action_log/dispatch-three.md` | fixture architecture on disk | receipt path under fixtures | independent read-back | done | none | fixture row | fixture-lane | 0 |
