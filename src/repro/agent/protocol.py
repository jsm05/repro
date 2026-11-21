"""Text action protocol (DESIGN 5.5): each reply holds exactly one fenced action block.

```action
{"tool": "bash", "args": {"cmd": "grep -rn parse_date src/"}}
```

The parser only checks structure. Whether the tool exists and its arguments are
valid is the tools' job, so those come back to the model as tool errors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# The closing fence is optional: some providers drop it at the end of a reply (D-M5-12).
_BLOCK = re.compile(r"```action[ \t]*\r?\n(.*?)(?:```|\Z)", re.DOTALL)

REPAIR = (
    "Your last reply had no valid action. Reply with exactly one fenced block:\n\n"
    "```action\n"
    '{"tool": "<bash|view|write_test|check|submit|abstain>", "args": {...}}\n'
    "```\n\nProblem: {problem}"
)
NUDGE = (
    "You have sent the same action three times in a row with the same result. Try "
    "something different. Sending it a fourth time ends the run."
)


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return json.dumps({"tool": self.tool, "args": self.args}, sort_keys=True)


def _bare_json(text: str) -> list[str]:
    """JSON objects with a "tool" key written without the fence (D-M5-12)."""
    dec = json.JSONDecoder()
    found, i = [], text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "tool" in obj:
            found.append(text[i:end])
        i = text.find("{", end)
    return found


def parse_action(text: str) -> Action:
    blocks = _BLOCK.findall(text or "")
    if not blocks:
        blocks = _bare_json(text or "")
    if not blocks:
        raise ParseError("no ```action block found")
    if len(blocks) > 1:
        raise ParseError(f"{len(blocks)} action blocks found; send exactly one per reply")
    try:
        obj = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise ParseError(f"the action block is not valid JSON ({exc.msg})") from None
    if not isinstance(obj, dict):
        raise ParseError("the action must be a JSON object")
    tool = obj.get("tool")
    args = obj.get("args", {})
    if not isinstance(tool, str) or not tool:
        raise ParseError('the action needs a "tool" string')
    if not isinstance(args, dict):
        raise ParseError('"args" must be a JSON object')
    extra = set(obj) - {"tool", "args"}
    if extra:
        raise ParseError(f"unexpected keys in the action: {', '.join(sorted(extra))}")
    return Action(tool, args)


def repair_message(problem: str) -> str:
    return REPAIR.replace("{problem}", problem)
