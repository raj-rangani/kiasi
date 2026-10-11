import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = "raj-rangani/kiasi"
SITE = "https://raj-rangani.github.io/kiasi"
ATOM = "http://www.w3.org/2005/Atom"

CODE = re.compile(r"(`[^`]+`)")
LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
LEAD = re.compile(r"^\*\*(.+?)\*\*")
COMPARE = re.compile(r"^\*\*Full changelog\*\*:\s*(https?://\S+)\s*$")


def link(match):
    text, url = match.group(1), match.group(2)
    if re.match(r"https?://", html.unescape(url)):
        return '<a href="%s">%s</a>' % (url, text)
    return match.group(0)


def inline(text):
    parts = CODE.split(html.escape(text))
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append("<code>%s</code>" % part[1:-1])
        else:
            part = LINK.sub(link, part)
            out.append(BOLD.sub(r"<b>\1</b>", part))
    return "".join(out)


def plain(text):
    return text.replace("`", "")


def parse_body(body):
    summary, compare, sections, current, item = [], "", [], None, None
    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        found = COMPARE.match(line)
        if found:
            compare = found.group(1)
            item = None
            continue
        if line.startswith("## "):
            current = {"heading": line[3:].strip(), "raw": []}
            sections.append(current)
            item = None
        elif current is None and not line.startswith("- "):
            if line:
                summary.append(line)
        elif current is None:
            current = {"heading": "Changes", "raw": []}
            sections.append(current)
            item = [line[2:].strip()]
            current["raw"].append(item)
        elif line.startswith("- "):
            item = [line[2:].strip()]
            current["raw"].append(item)
        elif line and item is not None:
            item.append(line)
        elif not line:
            item = None
    out = []
    for section in sections:
        items = []
        for parts in section["raw"]:
            text = " ".join(parts)
            lead = LEAD.match(text)
            items.append({"lead": plain(lead.group(1)) if lead else "", "html": inline(text)})
        out.append({"heading": section["heading"], "items": items})
    return inline(" ".join(summary)) if summary else "", compare, out


def convert(api):
    published = [r for r in api if not r.get("draft")]
    published.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    releases, latest_set = [], False
    for r in published:
        tag = r["tag_name"]
        version = tag[1:] if tag[:1] == "v" else tag
        stamp = r.get("published_at") or r.get("created_at") or ""
        summary, compare, sections = parse_body(r.get("body"))
        latest = not latest_set and not r.get("prerelease")
        latest_set = latest_set or latest
        releases.append({
            "version": version,
            "tag": tag,
            "date": stamp[:10],
            "stamp": stamp,
            "title": r.get("name") or "Kiasi " + version,
            "url": r.get("html_url") or "https://github.com/%s/releases/tag/%s" % (REPO, tag),
            "latest": latest,
            "summary": summary,
            "compare": compare,
            "sections": sections,
        })
    return releases


def entry_html(rel):
    parts = []
    if rel["summary"]:
        parts.append("<p>%s</p>" % rel["summary"])
    for section in rel["sections"]:
        parts.append("<h3>%s</h3>" % html.escape(section["heading"]))
        parts.append("<ul>%s</ul>" % "".join("<li>%s</li>" % i["html"] for i in section["items"]))
    if rel["compare"]:
        parts.append('<p><a href="%s">Full changelog</a></p>' % html.escape(rel["compare"]))
    return "".join(parts)


def atom(releases):
    ET.register_namespace("", ATOM)
    feed = ET.Element("{%s}feed" % ATOM)

    def add(parent, name, text=None, **attrs):
        el = ET.SubElement(parent, "{%s}%s" % (ATOM, name), attrs)
        if text is not None:
            el.text = text
        return el

    add(feed, "title", "Kiasi releases")
    add(feed, "id", SITE + "/releases/")
    add(feed, "updated", releases[0]["stamp"] if releases else "1970-01-01T00:00:00Z")
    add(feed, "link", href=SITE + "/releases/", rel="alternate")
    add(feed, "link", href=SITE + "/releases.xml", rel="self")
    for rel in releases:
        url = "%s/releases/#%s" % (SITE, rel["tag"])
        entry = add(feed, "entry")
        add(entry, "title", rel["title"])
        add(entry, "id", url)
        add(entry, "link", href=url, rel="alternate")
        add(entry, "updated", rel["stamp"])
        add(entry, "content", entry_html(rel), type="html")
    ET.indent(feed)
    return ET.tostring(feed, encoding="unicode", xml_declaration=False)


def main(argv):
    if len(argv) != 3:
        print("usage: site_releases.py <releases-api.json> <out-dir>", file=sys.stderr)
        return 2
    api = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    releases = convert(api)
    data = {"updated": releases[0]["date"] if releases else "", "releases": [{k: v for k, v in r.items() if k != "stamp"} for r in releases]}
    (out / "releases.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "releases.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n' + atom(releases) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
