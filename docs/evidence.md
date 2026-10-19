# Evidence

Measurements, coverage reports and sandbox runs that plan steps cite in their acceptance. Newest first within each step.

## Step 4: money engine port

### Coverage of the money path (2026-10-19, report only)

`make coverage` at the step-4 commits (53 backend tests, local PostgreSQL 18 container):

```
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src/tidehold/money/__init__.py            0      0      0      0   100%
src/tidehold/money/commands.py          111     10     38      9    87%   65, 79, 114, 143, 183, 206, 210, 267, 307-308
src/tidehold/money/paypal.py            149      9     16      3    93%   86, 97, 102, 157-158, 173->175, 208, 266, 269, 272
src/tidehold/money/service.py           116     22     40      8    78%   38, 53, 57, 85, 93, 100, 102-103, 112, 138-139, 187-190, 205-214
src/tidehold/settlement/__init__.py       0      0      0      0   100%
src/tidehold/settlement/plan.py          35      2     12      3    89%   37, 42, 51->exit
---------------------------------------------------------------------------------
TOTAL                                   411     43    106     23    86%
```

Read: the uncovered service lines are a checkout order refused at creation (187-190), a checkout attempt closed by a concurrent reconcile (205-214) and a reconcile that skips a test-card command without a card (138-139); `holds` has no code yet.

### Sandbox smoke and reauthorization check

Not run yet: needs the VM (D13 shared sandbox credentials). Commands: `python -m tidehold.bootstrap.sandbox smoke` and `reauth create|authorize|day4 STATE_FILE`.
