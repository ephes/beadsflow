# Autopilot protocol

`beadsflow` coordinates an implementer + reviewer loop using **comment markers** on each bead. This makes the state machine explicit in Beads, works across tools (Codex/Claude/etc.), and is easy to audit.

## Coordination markers

Approval requires exactly `LGTM` on the first nonempty line (surrounding whitespace is allowed). Quotes, bullets, Markdown wrappers, punctuation, lowercase variants, and later body approvals do not approve. An approval containing a `Changes requested:` verdict anywhere, including quoted examples, requests changes conservatively. The standalone helper preserves invalid output as diagnostics beneath `Changes requested:`. Non-approval coordination markers retain Markdown tolerance. Runner stdout comments are classified before and after truncation; if truncation changes the verdict, posting fails.

Use these exact markers as the first non-empty line of a comment:

- `Ready for review:` — implementer signals “I’m done, please review”.
- `LGTM` — reviewer approves; the bead is considered complete.
- `Changes requested:` — reviewer requests changes; implementer should take over again.

Only children of the selected epic are candidates for execution. Every blocking dependency reported by Beads must be closed before a child can run, including dependencies outside the epic. Open, in-progress, or blocked external dependencies prevent both new and resumed work. Readiness checks `dependency_type = "blocks"`; parent-child and association edges do not block execution. For older payloads with a missing, empty, or null dependency type, the dependency is treated as blocking.

## How beadsflow chooses the next phase

For a given bead, `beadsflow` looks at the **most recent marker** in the comment history:

- If the most recent marker is `LGTM`, the bead is complete and should be closed (or skipped if already closed).
- If the most recent marker is `Ready for review:` and there is no later `LGTM`, run the reviewer phase.
- If the most recent marker is `Changes requested:` (or there are no markers), run the implementer phase.

Comments are ordered chronologically by timestamp, with numeric comment ID as the
tie-breaker (larger IDs are newer). Input array order does not affect phase
selection or the helper's latest Ready for review / previous review context.
This ordering is separate from the fail-closed marker grammar above.

The Beads adapter validates consumed issue, dependency, child and comment fields
before returning context to selection or review. Malformed data stops the run
with a bounded `BeadsError` diagnostic before using that data for a comment,
close or profile invocation. See the [quickstart](quickstart.md) for the input contract.

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

## Runner review and failure boundaries

Runner review profiles require an available Git worktree and are fenced by the
existing Git signature before and after execution. Non-Git Beads projects stop
before reviewer launch. The configured Beads metadata subdirectory is excluded,
including tracked JSONL exports written by command-mode comments; the rest of the
entire worktree is covered, including outside a nested project root. Changed HEAD, tracked/staged patches or untracked content/modes stop
publication and phase acceptance without undoing edits or closing the issue. A
safe refusal comment is attempted and a private log preserves the reason and rejected output.
Ignored files are outside the signature, as are edits made after the final check
and transient changes restored before it. Prefer stdout mode so approval is
published by the runner after the check; command-mode profiles own their direct
Beads side effects and must be inspected before retrying a rejected review.

Automatic runner failures (including invalid or truncated verdicts) post only a
trusted phase/exit or verdict-validation error and private log reference. Raw process output stays in
the protected local log, not failure comments or default console errors. A failed
comment attempt preserves the primary failure. Valid stdout comments are still
published as configured. The standalone review helper's output is not itself a
runner execution fence.
