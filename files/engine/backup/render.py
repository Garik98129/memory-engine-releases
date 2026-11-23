"""Notion pages, rows and blocks as Markdown text (docs/DESIGN.md §9).

Text only: no attachments and no temporary file links — a Notion-hosted file is named,
an outside link is kept as a link. Deterministic: the same Notion gives the same bytes,
so a night without changes writes nothing but the stamp. Shapes follow API version
2025-09-03 (engine/backup/notion.py); a block or property type this file does not know
is rendered from its text if it has any, else named — never dropped silently (P5).
"""
from __future__ import annotations

import json
from typing import Callable

MAX_DEPTH = 25          # nested children below this are named, not followed
REMOVED = "[file]"      # a Notion-hosted attachment: its link is temporary and signed


def _s(value: object) -> str:
    return value if isinstance(value, str) else ""


def text(items: object) -> str:
    """A rich-text array as one line of text; a link in the text stays a link."""
    if not isinstance(items, list):
        return ""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        plain = _s(item.get("plain_text"))
        link = None
        if item.get("type") == "text":
            link = _s(((item.get("text") or {}).get("link") or {}).get("url"))
        out.append(f"[{plain}]({link})" if link and plain else plain)
    return "".join(out).replace("\r", "")


def _user(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return _s(value.get("name")) or _s(value.get("id"))


def _date(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    start, end = _s(value.get("start")), _s(value.get("end"))
    return f"{start} → {end}" if end else start


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _file(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    name = _s(value.get("name"))
    if value.get("type") == "external":
        url = _s((value.get("external") or {}).get("url"))
        return f"{name} ({url})" if name and url else name or url
    return name or REMOVED


def _typed(value: object) -> str:
    """A formula or rollup result: {"type": t, t: ...}."""
    if not isinstance(value, dict):
        return ""
    t = _s(value.get("type"))
    inner = value.get(t)
    if t in ("string",):
        return _s(inner)
    if t == "number":
        return _number(inner)
    if t == "boolean":
        return "" if inner is None else ("yes" if inner else "no")
    if t == "date":
        return _date(inner)
    if t == "array" and isinstance(inner, list):
        return ", ".join(x for x in (_typed(i) if isinstance(i, dict) and "type" in i else "" for i in inner) if x)
    if t in ("unsupported", "incomplete"):
        return f"[{t}]"
    return ""


def prop(value: object) -> str:
    """One property value as one line of text."""
    if not isinstance(value, dict):
        return ""
    t = _s(value.get("type"))
    v = value.get(t)
    if t in ("title", "rich_text"):
        return text(v)
    if t == "number":
        return _number(v)
    if t in ("select", "status"):
        return _s((v or {}).get("name")) if isinstance(v, dict) else ""
    if t == "multi_select":
        return ", ".join(_s(o.get("name")) for o in v if isinstance(o, dict)) if isinstance(v, list) else ""
    if t == "date":
        return _date(v)
    if t == "people":
        return ", ".join(x for x in (_user(u) for u in v) if x) if isinstance(v, list) else ""
    if t == "files":
        return ", ".join(x for x in (_file(f) for f in v) if x) if isinstance(v, list) else ""
    if t == "checkbox":
        return "yes" if v else "no"
    if t in ("url", "email", "phone_number", "created_time", "last_edited_time"):
        return _s(v)
    if t in ("formula", "rollup"):
        return _typed(v)
    if t == "relation":
        ids = ", ".join(_s(r.get("id")) for r in v if isinstance(r, dict)) if isinstance(v, list) else ""
        return f"{ids} …" if value.get("has_more") else ids
    if t in ("created_by", "last_edited_by"):
        return _user(v)
    if t == "unique_id" and isinstance(v, dict):
        prefix, number = _s(v.get("prefix")), v.get("number")
        number = "" if number is None else str(number)
        return f"{prefix}-{number}" if prefix and number else number
    if t == "verification":
        return _s((v or {}).get("state")) if isinstance(v, dict) else ""
    if t == "button":
        return ""
    if t == "place" and isinstance(v, dict):
        return " — ".join(x for x in (_s(v.get("name")), _s(v.get("address"))) if x)
    if v is None:
        return ""
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def properties(props: object) -> list[str]:
    """«- **Name:** value» lines, the title first, the rest by name; an empty value is
    kept as an empty line, so a cleared property shows as a change."""
    if not isinstance(props, dict):
        return []
    names = sorted(props)
    titles = [n for n in names if isinstance(props[n], dict) and props[n].get("type") == "title"]
    ordered = titles + [n for n in names if n not in titles]
    return [f"- **{n}:** {prop(props[n])}".rstrip() for n in ordered]


def title_of(obj: dict) -> str:
    """The title of a data source (its title array) or of a page (its title property —
    a page has no title of its own, and a data source's properties are its schema)."""
    if isinstance(obj.get("title"), list):
        return text(obj.get("title"))
    props = obj.get("properties")
    if isinstance(props, dict):
        for value in props.values():
            if isinstance(value, dict) and value.get("type") == "title":
                return text(value.get("title"))
    return ""


# -- blocks -------------------------------------------------------------------------------

_HEADINGS = {"heading_1": "#", "heading_2": "##", "heading_3": "###", "heading_4": "####"}
_FILES = ("image", "video", "audio", "file", "pdf")
_LINKS = ("bookmark", "embed", "link_preview")
_WRAPPERS = ("column_list", "column", "synced_block")   # children at the same level


def _icon(value: object) -> str:
    return _s(value.get("emoji")) if isinstance(value, dict) else ""


def _parent_ref(block: dict) -> str:
    for key in ("page_id", "database_id", "data_source_id", "block_id"):
        if key in block:
            return f"{key} {_s(block.get(key))}"
    return ""


def _table(block: dict, children: list[dict], indent: str) -> list[str]:
    lines = []
    for i, row in enumerate(children):
        cells = (row.get("table_row") or {}).get("cells") if row.get("type") == "table_row" else None
        if not isinstance(cells, list):
            continue
        lines.append(indent + "| " + " | ".join(text(c).replace("|", "\\|") for c in cells) + " |")
        if i == 0:
            lines.append(indent + "|" + "---|" * max(len(cells), 1))
    return lines


def block_lines(block: dict, children_of: Callable[[str], list[dict]], depth: int = 0,
                seen: set[str] | None = None) -> list[str]:
    """A block and, where it has them, its children — one level fetched at a time."""
    seen = set() if seen is None else seen
    t = _s(block.get("type"))
    b = block.get(t) if isinstance(block.get(t), dict) else {}
    rt = text(b.get("rich_text"))
    indent = "  " * depth
    own: list[str] = []
    if t == "paragraph":
        own = [indent + rt if rt else ""]
    elif t in _HEADINGS:
        own = [f"{indent}{_HEADINGS[t]} {rt}"]
    elif t == "bulleted_list_item":
        own = [f"{indent}- {rt}"]
    elif t == "numbered_list_item":
        own = [f"{indent}1. {rt}"]
    elif t == "to_do":
        own = [f"{indent}- [{'x' if b.get('checked') else ' '}] {rt}"]
    elif t == "toggle":
        own = [f"{indent}▸ {rt}"]
    elif t == "quote":
        own = [f"{indent}> {rt}"]
    elif t == "callout":
        icon = _icon(b.get("icon"))
        own = [f"{indent}> {icon + ' ' if icon else ''}{rt}"]
    elif t == "code":
        lang = _s(b.get("language"))
        own = [f"{indent}```{lang}", *[indent + line for line in rt.split("\n")], f"{indent}```"]
        caption = text(b.get("caption"))
        if caption:
            own.append(f"{indent}{caption}")
    elif t == "divider":
        own = [f"{indent}---"]
    elif t == "equation":
        own = [f"{indent}$$ {_s(b.get('expression'))} $$"]
    elif t in _LINKS:
        url = _s(b.get("url"))
        caption = text(b.get("caption"))
        own = [f"{indent}[{caption or url}]({url})" if url else f"{indent}[{t}]"]
    elif t in _FILES:
        name = _file({**b, "name": _s(b.get("name"))}) if b.get("type") == "external" else (_s(b.get("name")) or REMOVED)
        caption = text(b.get("caption"))
        own = [f"{indent}[{t}: {name}]" + (f" {caption}" if caption else "")]
    elif t in ("child_page", "child_database"):
        own = [f"{indent}[{t.split('_')[1]}: {_s(b.get('title'))}]"]
    elif t == "link_to_page":
        own = [f"{indent}[link to {_parent_ref(b) or 'a page'}]"]
    elif t == "table":
        if block.get("has_children") and _s(block.get("id")):
            own = _table(block, children_of(block["id"]), indent)
        return own
    elif t == "table_row":
        own = _table({}, [block], indent)
    elif t in _WRAPPERS or t in ("table_of_contents", "breadcrumb"):
        own = []
    elif t == "template":
        own = [f"{indent}{rt}"] if rt else []
    elif t == "unsupported":
        own = [f"{indent}[unsupported: {_s(b.get('block_type')) or '?'}]"]
    else:
        fallback = rt or text(b.get("title")) or _s(b.get("title"))
        own = [f"{indent}{fallback}" if fallback else f"{indent}[{t or 'block'}]"]

    block_id = _s(block.get("id"))
    if not block.get("has_children") or t in ("child_page", "child_database") or not block_id:
        return own
    if block_id in seen or depth >= MAX_DEPTH:
        return own + [f"{indent}  [children not followed]"]
    seen.add(block_id)
    below = depth if t in _WRAPPERS else depth + 1
    for child in children_of(block_id):
        if isinstance(child, dict):
            own += block_lines(child, children_of, below, seen)
    return own


def content(children_of: Callable[[str], list[dict]], page_id: str) -> list[str]:
    lines: list[str] = []
    seen = {page_id}
    for child in children_of(page_id):
        if isinstance(child, dict):
            lines += block_lines(child, children_of, 0, seen)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# -- documents ----------------------------------------------------------------------------

def _parent(obj: dict) -> str:
    parent = obj.get("parent")
    return _parent_ref(parent) if isinstance(parent, dict) else ""


def page_markdown(page: dict, body: list[str], source: str = "") -> str:
    """One page or data source row as a document: title, where it is, its properties,
    its content."""
    title = title_of(page) or "Untitled"
    head = [f"# {title}", "",
            f"- id: {_s(page.get('id'))}",
            f"- url: {_s(page.get('url'))}",
            f"- parent: {_parent(page)}",
            f"- created: {_s(page.get('created_time'))}",
            f"- edited: {_s(page.get('last_edited_time'))}"]
    if source:
        head.append(f"- data source: {source}")
    props = properties(page.get("properties"))
    doc = head + ([""] + props if props else [])
    if body:
        doc += ["", "---", ""] + body
    return "\n".join(doc).rstrip("\n") + "\n"


def source_markdown(source: dict) -> str:
    """A data source itself: its title, where it is, its schema — one file beside its rows."""
    title = title_of(source) or "Untitled"
    schema = source.get("properties")
    lines = [f"# {title}", "",
             f"- id: {_s(source.get('id'))}",
             f"- url: {_s(source.get('url'))}",
             f"- parent: {_parent(source)}",
             f"- database parent: {_parent({'parent': source.get('database_parent')})}"]
    if isinstance(schema, dict) and schema:
        lines += ["", "## Properties", ""]
        lines += [f"- {name}: {_schema_line(schema[name])}".rstrip() for name in sorted(schema)]
    return "\n".join(lines) + "\n"


def _schema_line(definition: object) -> str:
    """A property's type and what else its definition carries: the options of a select,
    the target of a relation, the formula, the rollup — so a change of them is a change
    of the file."""
    if not isinstance(definition, dict):
        return ""
    t = _s(definition.get("type"))
    inner = definition.get(t) if isinstance(definition.get(t), dict) else {}
    extra = ""
    if t in ("select", "multi_select", "status"):
        options = inner.get("options")
        names = sorted(_s(o.get("name")) for o in options if isinstance(o, dict)) if isinstance(options, list) else []
        extra = ", ".join(n for n in names if n)
    elif t == "relation":
        extra = _s(inner.get("data_source_id")) or _s(inner.get("database_id"))
    elif t == "formula":
        extra = _s(inner.get("expression"))
    elif t == "rollup":
        extra = " ".join(x for x in (_s(inner.get("function")), _s(inner.get("relation_property_name")),
                                     _s(inner.get("rollup_property_name"))) if x)
    elif t == "number":
        extra = _s(inner.get("format"))
    elif t == "unique_id":
        extra = _s(inner.get("prefix"))
    return f"{t} ({extra})" if extra else t
