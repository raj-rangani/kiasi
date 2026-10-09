"""Append an approved "Share your month" issue to docs/results.json. Run by .github/workflows/results.yml."""
import json
import os
import pathlib
import re
import sys

body = os.environ["BODY"]
sections = {m.group(1).strip(): m.group(2).strip()
            for m in re.finditer(r"^### (.+?)\n\n(.*?)(?=^### |\Z)", body, re.S | re.M)}


def clean(value):
    value = re.sub(r"^```\w*\n|\n```$", "", value.strip()).strip()
    return "" if value == "_No response_" else value


card = clean(sections.get("Your month card", ""))
if not card.startswith("Kiasi, last"):
    sys.exit("The card does not start with 'Kiasi, last'; nothing added.")
if "[x]" not in sections.get("Show it", "").lower():
    sys.exit("The consent box is not ticked; nothing added.")

entry = {
    "issue": int(os.environ["NUMBER"]),
    "handle": os.environ["HANDLE"],
    "date": os.environ["CREATED"][:10],
    "plan": clean(sections.get("Plan", "")),
    "workload": clean(sections.get("What you mostly use Claude Code for", "")),
    "note": clean(sections.get("Anything to add", ""))[:280],
    "card": "\n".join(card.splitlines()[:12]),
}
path = pathlib.Path("docs/results.json")
data = [e for e in json.loads(path.read_text()) if e.get("issue") != entry["issue"]]
data.append(entry)
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"Added the card from #{entry['issue']} by {entry['handle']}; {len(data)} cards now.")
