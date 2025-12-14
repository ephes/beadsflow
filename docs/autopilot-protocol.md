# Autopilot protocol

`beadsflow` coordinates an implementer + reviewer loop using **comment markers** on each bead. This makes the state machine explicit in Beads, works across tools (Codex/Claude/etc.), and is easy to audit.

## Coordination markers

Use these exact markers as the first non-empty line of a comment:

- `Ready for review:` — implementer signals “I’m done, please review”.
- `LGTM` — reviewer approves; the bead is considered complete.
- `Changes requested:` — reviewer requests changes; implementer should take over again.

## How beadsflow chooses the next phase

For a given bead, `beadsflow` looks at the **most recent marker** in the comment history:

- If the most recent marker is `LGTM`, the bead is complete and should be closed (or skipped if already closed).
- If the most recent marker is `Ready for review:` and there is no later `LGTM`, run the reviewer phase.
- If the most recent marker is `Changes requested:` (or there are no markers), run the implementer phase.

## Minimal end-to-end example

This shows the smallest practical “implement → review → approve” loop with commands you can run locally.

### Implementer

**bash/zsh**
```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"

issue="<issue-id>"
bd --no-daemon --no-db show "$issue"

# ...make changes...

just test
just typecheck
just lint

bd --no-daemon comment "$issue" $'Ready for review:\n\nSummary:\n- <what changed>\n\nValidation:\n- just test\n- just typecheck\n- just lint'
```

**fish**
```fish
set -gx BEADS_NO_DAEMON 1
set -gx BEADS_DIR "$PWD/.beads"

set issue <issue-id>
bd --no-daemon --no-db show $issue

# ...make changes...

just test
just typecheck
just lint

set msg (string join \n \
  'Ready for review:' '' \
  'Summary:' \
  '- <what changed>' '' \
  'Validation:' \
  '- just test' \
  '- just typecheck' \
  '- just lint')
bd --no-daemon comment $issue $msg
```

### Reviewer

**bash/zsh**
```bash
export BEADS_NO_DAEMON=1
export BEADS_DIR="$PWD/.beads"

issue="<issue-id>"
bd --no-daemon --no-db show "$issue"

# ...review changes...

just test
just typecheck
just lint

bd --no-daemon comment "$issue" "LGTM"
# or:
bd --no-daemon comment "$issue" $'Changes requested:\n- <actionable change 1>\n- <actionable change 2>'
```

**fish**
```fish
set -gx BEADS_NO_DAEMON 1
set -gx BEADS_DIR "$PWD/.beads"

set issue <issue-id>
bd --no-daemon --no-db show $issue

# ...review changes...

just test
just typecheck
just lint

set changes (string join \n \
  'Changes requested:' \
  '- <actionable change 1>' \
  '- <actionable change 2>')
bd --no-daemon comment $issue LGTM
# or:
bd --no-daemon comment $issue $changes
```
