#!/usr/bin/env python3
"""Build the standalone interface map from the reviewed Markdown; no dependencies."""
from collections import Counter
from html import escape
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = DOCS / "interface-map.md"
STATUSES = {"bygget": "Bygget", "delvist": "Delvist", "mangler": "Mangler", "opdagelse": "Kun opdagelse"}
TOKEN = re.compile(r"`[^`]+`|\*\*.+?\*\*|\[[^\]]+\]\([^)]+\)")


def inline(text):
    parts, offset = [], 0
    for match in TOKEN.finditer(text):
        parts.append(escape(text[offset:match.start()]))
        token = match[0]
        if token.startswith("`"):
            parts.append(f"<code>{escape(token[1:-1])}</code>")
        elif token.startswith("**"):
            parts.append(f"<strong>{inline(token[2:-2])}</strong>")
        else:
            label, url = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()
            if not url.startswith(("../", "./", "#", "https://")):
                raise ValueError(f"Unsupported link: {url}")
            parts.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
        offset = match.end()
    parts.append(escape(text[offset:]))
    return "".join(parts)


def prose(lines):
    result, paragraph, list_kind = [], [], None

    def flush_paragraph():
        if paragraph:
            result.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    for line in [*lines, ""]:
        line = line.strip()
        match = re.match(r"(?:(-) |\d+\. )(.+)", line)
        if match:
            flush_paragraph()
            kind = "ul" if match[1] else "ol"
            if list_kind != kind:
                if list_kind:
                    result.append(f"</{list_kind}>")
                result.append(f"<{kind}>")
                list_kind = kind
            result.append(f"<li>{inline(match[2])}</li>")
        else:
            if list_kind:
                result.append(f"</{list_kind}>")
                list_kind = None
            if line:
                paragraph.append(line)
            else:
                flush_paragraph()
    return "\n".join(result)


def table_rows(lines):
    table = [line for line in lines if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in table[2:]]


def slug(text):
    text = text.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def plain(text):
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", text)).strip()


def build():
    sections, heading = {}, "intro"
    for line in SOURCE.read_text().splitlines()[1:]:
        if line.startswith("## "):
            heading = line[3:]
        else:
            sections.setdefault(heading, []).append(line)
    counts = Counter()
    groups = {}
    for title, system in [("SkoleIntra", "skoleintra"), ("SFO og Tabulex", "sfo")]:
        cards = []
        rows = table_rows(sections[title])
        for row in rows:
            if system == "skoleintra":
                name, data, actions, api, gap = row
            else:
                name, data, api, gap = row
                actions = ""
            name = plain(name)
            status = next((key for key, label in STATUSES.items() if api.startswith("**" + label)), None)
            if status is None:
                raise ValueError(f"Unknown status: {api}")
            counts[status] += 1
            identifier = system + "-" + slug(name)
            search = escape(plain(" ".join(row)), quote=True)
            actions_html = f'<div class="fact"><dt>Handlinger i interfacet</dt><dd>{inline(actions)}</dd></div>' if actions else ""
            cards.append(f'''<details class="feature" id="{identifier}" data-system="{system}" data-status="{status}" data-search="{search}">
  <summary><div class="card-heading"><span class="status {status}">{STATUSES[status]}</span><span class="expand" aria-hidden="true">+</span></div>
    <h3>{escape(name)}</h3><span class="card-preview">{escape(plain(data))}</span><span class="summary-label" aria-hidden="true">Se data og handlinger <span>↗</span></span></summary>
  <div class="card-details"><dl>
    <div class="fact"><dt>{'Datapunkter' if actions else 'Data og observerede handlinger'}</dt><dd>{inline(data)}</dd></div>
    {actions_html}<div class="fact"><dt>Biblioteket nu</dt><dd>{inline(api)}</dd></div>
    <div class="gap"><dt>Det væsentlige hul</dt><dd>{inline(gap)}</dd></div>
  </dl><a class="permalink" href="#{identifier}">Link til dette område <span aria-hidden="true">↗</span></a></div>
</details>''')
        groups[system] = (len(rows), "\n".join(cards))

    next_steps = []
    for index, (name, benefit, deliverable, scope) in enumerate(table_rows(sections["Forslag til næste skridt"]), 1):
        next_steps.append(f'''<article class="proposal"><span class="proposal-number">{index:02}</span><div><h3>{inline(name)}</h3><p>{inline(benefit)}</p><dl><dt>Første leverance</dt><dd>{inline(deliverable)}</dd><dt>Omfang og usikkerhed</dt><dd>{inline(scope)}</dd></dl></div></article>''')

    source = SOURCE.read_text()
    reviewed, commit, version = re.search(r"Gennemgået \*\*([^*]+)\*\* mod commit \*\*`([^`]+)`\*\*, pakkeversion \*\*([^*]+)\*\*", source).groups()
    stats = "\n".join(f'''<button type="button" class="stat {key}" data-filter-value="{key}" aria-label="Vis områder med status {label}"><span class="stat-number">{counts[key]:02}</span><span class="stat-label"><span class="dot"></span>{label}</span><span class="stat-arrow" aria-hidden="true">↗</span></button>''' for key, label in STATUSES.items())
    filters = '<button type="button" class="filter-chip active" data-status-filter="all" aria-pressed="true">Alle <span>' + str(sum(counts.values())) + '</span></button>'
    filters += "\n".join(f'<button type="button" class="filter-chip" data-status-filter="{key}" aria-pressed="false">{label} <span>{counts[key]}</span></button>' for key, label in STATUSES.items())

    values = {
        "REVIEWED": escape(reviewed), "COMMIT": escape(commit), "VERSION": escape(version),
        "INTRO": prose(sections["intro"]), "TOTAL": str(sum(counts.values())),
        "STATS": stats, "FILTERS": filters, "SCHOOL_COUNT": str(groups["skoleintra"][0]),
        "SFO_COUNT": str(groups["sfo"][0]), "SCHOOL_CARDS": groups["skoleintra"][1],
        "SFO_CARDS": groups["sfo"][1], "SFO_INTRO": prose([line for line in sections["SFO og Tabulex"] if not line.startswith("|")]),
        "PROPOSALS": "\n".join(next_steps),
        "PROPOSAL_INTRO": prose([line for line in sections["Forslag til næste skridt"] if not line.startswith("|")]),
        "LEGEND": prose(sections["Sådan læses kortet"]),
        "NUANCES": prose(sections["Vigtige nuancer ved eksisterende funktioner"]),
        "LIMITATIONS": prose(sections["Observationer og begrænsninger"]),
        "SOURCES": prose(sections["Lokale kilder og videre brug"]),
    }
    template = (DOCS / "interface-map.template.html").read_text()
    placeholders = set(re.findall(r"@@([A-Z_]+)@@", template))
    assert placeholders <= values.keys(), placeholders - values.keys()
    result = re.sub(r"@@([A-Z_]+)@@", lambda match: values[match[1]], template)
    (DOCS / "interface-map.html").write_text(result)
    print(f"Built docs/interface-map.html: {sum(counts.values())} areas, {len(next_steps)} proposals")


if __name__ == "__main__":
    build()
