import unittest

from engine.backup import render
from engine.backup.tests.fakenotion import paragraph, rich


def value(t, v, **extra):
    return {"id": "x", "type": t, t: v, **extra}


class Properties(unittest.TestCase):
    def test_each_property_type_renders_to_one_line(self):
        cases = [
            (value("title", rich("Visa")), "Visa"),
            (value("rich_text", rich("see", "https://x.example")), "[see](https://x.example)"),
            (value("number", 8), "8"), (value("number", 2.5), "2.5"), (value("number", None), ""),
            (value("select", {"name": "Car"}), "Car"), (value("status", None), ""),
            (value("multi_select", [{"name": "a"}, {"name": "b"}]), "a, b"),
            (value("date", {"start": "2030-01-07", "end": None}), "2030-01-07"),
            (value("date", {"start": "2026-01-01", "end": "2026-01-02"}), "2026-01-01 → 2026-01-02"),
            (value("people", [{"object": "user", "id": "u1", "name": "Alex"}, {"object": "user", "id": "u2"}]), "Alex, u2"),
            (value("files", [{"name": "scan.pdf", "type": "file", "file": {"url": "https://s3/signed", "expiry_time": "x"}},
                              {"name": "site", "type": "external", "external": {"url": "https://ex.example/a"}}]),
             "scan.pdf, site (https://ex.example/a)"),
            (value("checkbox", True), "yes"), (value("url", "https://u"), "https://u"),
            (value("email", "a@b.c"), "a@b.c"), (value("phone_number", "+972"), "+972"),
            (value("formula", {"type": "number", "number": 16}), "16"),
            (value("formula", {"type": "unsupported", "unsupported": {}}), "[unsupported]"),
            (value("relation", [{"id": "p1"}, {"id": "p2"}], has_more=True), "p1, p2 …"),
            (value("rollup", {"type": "array", "function": "show_original", "array": [{"type": "number", "number": 1}, {"type": "number", "number": 2}]}), "1, 2"),
            (value("created_time", "2026-09-01T09:00:00.000Z"), "2026-09-01T09:00:00.000Z"),
            (value("created_by", {"object": "user", "id": "u9"}), "u9"),
            (value("unique_id", {"prefix": "DOC", "number": 42}), "DOC-42"),
            (value("verification", {"state": "verified"}), "verified"),
            (value("button", {}), ""),
            (value("place", {"name": "Home", "address": "Springfield"}), "Home — Springfield"),
            (value("brand_new_type", {"k": 1}), '{"k": 1}'),
        ]
        for given, want in cases:
            with self.subTest(given["type"]):
                self.assertEqual(render.prop(given), want)

    def test_a_signed_file_link_is_not_copied(self):
        line = render.prop(value("files", [{"name": "", "type": "file", "file": {"url": "https://s3/signed?sig=1"}}]))
        self.assertEqual(line, "[file]")

    def test_the_title_comes_first_and_the_rest_by_name(self):
        props = {"Zeta": value("number", 1), "Name": value("title", rich("T")), "Alpha": value("checkbox", False)}
        self.assertEqual(render.properties(props), ["- **Name:** T", "- **Alpha:** no", "- **Zeta:** 1"])


class Blocks(unittest.TestCase):
    def children_of(self, tree):
        def register(blocks):
            for b in blocks:
                kids = b.get("_children") or []
                if kids:
                    tree[b["id"]] = kids
                    register(kids)
        for blocks in list(tree.values()):
            register(blocks)

        def children(bid):
            return [{k: v for k, v in b.items() if k != "_children"} for b in tree.get(bid, [])]
        return children

    def test_lists_nest_tables_render_and_files_are_named(self):
        tree = {"p": [
            {"id": "h", "type": "heading_2", "has_children": False, "heading_2": {"rich_text": rich("Docs")}},
            {"id": "l1", "type": "bulleted_list_item", "has_children": True, "bulleted_list_item": {"rich_text": rich("one")},
             "_children": [{"id": "l2", "type": "to_do", "has_children": False, "to_do": {"rich_text": rich("two"), "checked": True}}]},
            {"id": "t", "type": "table", "has_children": True, "table": {"table_width": 2, "has_column_header": True},
             "_children": [{"id": "r1", "type": "table_row", "table_row": {"cells": [rich("a"), rich("b|c")]}},
                           {"id": "r2", "type": "table_row", "table_row": {"cells": [rich("1"), rich("2")]}}]},
            {"id": "i", "type": "image", "has_children": False, "image": {"type": "file", "file": {"url": "https://s3/x?sig"}, "caption": rich("passport")}},
            {"id": "c", "type": "child_page", "has_children": True, "child_page": {"title": "Sub"}},
            {"id": "u", "type": "unsupported", "has_children": False, "unsupported": {"block_type": "form"}},
            {"id": "n", "type": "future_block", "has_children": False, "future_block": {"rich_text": rich("still here")}},
            {"id": "e", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": []}},
        ]}
        lines = render.content(self.children_of(tree), "p")
        self.assertEqual(lines, ["## Docs", "- one", "  - [x] two", "| a | b\\|c |", "|---|---|", "| 1 | 2 |",
                                 "[image: [file]] passport", "[page: Sub]", "[unsupported: form]", "still here"])

    def test_a_cycle_or_too_deep_a_nest_is_named_not_followed(self):
        a = {"id": "a", "type": "paragraph", "has_children": True, "paragraph": {"rich_text": rich("a")}}
        lines = render.content(lambda bid: [a] if bid in ("p", "a") else [], "p")   # a is its own child
        self.assertEqual(lines, ["a", "  a", "    [children not followed]"])
        deep = paragraph("d0", "0")
        node = deep
        for i in range(1, render.MAX_DEPTH + 3):
            child = paragraph(f"d{i}", str(i))
            node["_children"], node["has_children"] = [child], True
            node = child
        lines = render.content(self.children_of({"p": [deep]}), "p")
        self.assertEqual(lines[-1].strip(), "[children not followed]")
        self.assertEqual(len(lines), render.MAX_DEPTH + 2)

    def test_a_page_document_has_head_properties_and_body(self):
        page = {"id": "p1", "url": "https://n/p1", "parent": {"type": "page_id", "page_id": "root"},
                "created_time": "c", "last_edited_time": "e",
                "properties": {"Name": {"type": "title", "title": rich("Visa")}, "Until": {"type": "date", "date": {"start": "2030-01-07"}}}}
        doc = render.page_markdown(page, ["Body line"], source="Documents")
        self.assertEqual(doc, "# Visa\n\n- id: p1\n- url: https://n/p1\n- parent: page_id root\n- created: c\n- edited: e\n"
                              "- data source: Documents\n\n- **Name:** Visa\n- **Until:** 2030-01-07\n\n---\n\nBody line\n")

    def test_a_schema_change_changes_the_source_file(self):
        schema = {"Kind": {"type": "select", "select": {"options": [{"name": "Car"}, {"name": "Visa"}]}},
                  "Owner": {"type": "relation", "relation": {"data_source_id": "ds9"}},
                  "Due": {"type": "formula", "formula": {"expression": "dateAdd(prop(\"Issued\"), 10, \"years\")"}},
                  "Count": {"type": "rollup", "rollup": {"function": "count", "relation_property_name": "Owner", "rollup_property_name": "Name"}},
                  "Name": {"type": "title", "title": {}}}
        text = render.source_markdown({"id": "ds1", "title": rich("Documents"), "properties": schema})
        self.assertIn("- Kind: select (Car, Visa)", text)
        self.assertIn("- Owner: relation (ds9)", text)
        self.assertIn('- Due: formula (dateAdd(prop("Issued"), 10, "years"))', text)
        self.assertIn("- Count: rollup (count Owner Name)", text)
        self.assertIn("- Name: title\n", text)
        schema["Kind"]["select"]["options"].append({"name": "Passport"})
        self.assertNotEqual(text, render.source_markdown({"id": "ds1", "title": rich("Documents"), "properties": schema}))

    def test_the_same_notion_gives_the_same_bytes(self):
        page = {"id": "p1", "properties": {"B": {"type": "number", "number": 1}, "A": {"type": "number", "number": 2}}}
        self.assertEqual(render.page_markdown(page, []), render.page_markdown(dict(reversed(list(page.items()))), []))


if __name__ == "__main__":
    unittest.main()
