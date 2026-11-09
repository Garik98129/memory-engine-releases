import contextlib
import errno
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from engine.backup import copy as copying
from engine.backup import run
from engine.backup.notion import NotionError
from engine.backup.tests.fakenotion import FakeNotion, page, paragraph, rich, source
from engine.checks.tests.helpers import VALID_CONFIG, Repo

DS = "11111111111111111111111111111111"
DB = "dddddddddddddddddddddddddddddddd"
ROOT = "r0000000000000000000000000000000"
REPO_ROOT = Path(__file__).resolve().parents[3]   # where `engine` imports from
CARD = "4111 1111 1111 1111"          # a test number that passes Luhn


def world():
    docs = source(DS, "Documents", DB, ROOT, schema={"Name": {"id": "title", "type": "title", "title": {}},
                                                   "Number": {"id": "n", "type": "rich_text", "rich_text": {}}})
    rows = [page("row1", "Passport", {"type": "data_source_id", "data_source_id": DS},
                 {"Number": {"id": "n", "type": "rich_text", "rich_text": rich("12345678")}}),
            page("row2", "Driving licence", {"type": "data_source_id", "data_source_id": DS})]
    pages = [page(ROOT, "Home", {"type": "workspace", "workspace": True}),
             page("child", "Notes", {"type": "page_id", "page_id": ROOT}),
             page("row1", "Passport", {"type": "data_source_id", "data_source_id": DS}),   # search lists rows too
             page("gone", "Old", {"type": "page_id", "page_id": ROOT}, archived=True)]
    api = FakeNotion(pages=pages, sources=[docs], rows={DS: rows},
                     blocks={ROOT: [paragraph("b1", "Welcome")], "row1": [paragraph("b2", "valid until 2030")]})
    return api


class Collect(unittest.TestCase):
    def test_rows_and_pages_land_once_each_and_archived_ones_not_at_all(self):
        got = copying.collect(world(), set(), ["card"])
        self.assertEqual(sorted(got.files), [f"data/{DS}/_source.md", f"data/{DS}/row1.md", f"data/{DS}/row2.md",
                                             "pages/child.md", f"pages/{ROOT}.md"])
        self.assertEqual((got.pages, got.rows, got.sources, got.records, got.excluded), (2, 2, 1, 4, 0))
        self.assertIn("valid until 2030", got.files[f"data/{DS}/row1.md"])
        self.assertIn("- data source: Documents", got.files[f"data/{DS}/row1.md"])
        self.assertIn("## Properties\n\n- Name: title\n- Number: rich_text\n", got.files[f"data/{DS}/_source.md"])
        self.assertEqual(got.bases, {DS: {"rows": 2}})                                 # N3 per base, no titles

    def test_exclusion_covers_a_page_its_children_and_a_whole_data_source(self):
        got = copying.collect(world(), {ROOT}, ["card"])
        self.assertEqual(sorted(got.files), [])          # everything hangs under the home page
        self.assertEqual(got.excluded, 4)                 # the source, its two rows, the child page... and the home page
        got = copying.collect(world(), {DS.replace("1", "1")}, ["card"])
        self.assertEqual(sorted(got.files), ["pages/child.md", f"pages/{ROOT}.md"])

    def test_exclusion_by_database_id_and_with_dashes(self):
        dashed = f"{DB[:8]}-{DB[8:12]}-{DB[12:16]}-{DB[16:20]}-{DB[20:]}"
        got = copying.collect(world(), {dashed}, ["card"])
        self.assertEqual(sorted(got.files), ["pages/child.md", f"pages/{ROOT}.md"])

    def test_a_page_inside_a_block_of_an_excluded_page_is_excluded_through_the_block_chain(self):
        api = world()
        api.pages.append(page("inner", "In a column", {"type": "block_id", "block_id": "col"}))
        api.parents["col"] = {"type": "block_id", "block_id": "cols"}
        api.parents["cols"] = {"type": "page_id", "page_id": "child"}
        got = copying.collect(api, {"child"}, ["card"])
        self.assertNotIn("pages/inner.md", got.files)
        self.assertNotIn("pages/child.md", got.files)
        self.assertIn(f"pages/{ROOT}.md", got.files)

    # Issue #18, item 45 (Kimi's review of the owner's own copy, notion-backup PR #1): with
    # exclusions set, a page whose place cannot be followed to the top may hang under an
    # excluded one, so it is not copied «just in case» — the night is an error instead.

    def test_an_unreadable_block_chain_stops_the_copy(self):
        api = world()
        api.pages.append(page("inner", "In a column", {"type": "block_id", "block_id": "missing"}))
        with self.assertRaises(NotionError) as cm:
            copying.collect(api, {"child"}, ["card"])
        self.assertIn("inner: a block above it could not be read (NotionError)", str(cm.exception))

    def test_an_ancestor_not_shared_with_the_integration_stops_the_copy(self):
        api = world()
        api.pages.append(page("stray", "Shared alone", {"type": "page_id", "page_id": "unshared"}))
        with self.assertRaises(NotionError) as cm:
            copying.collect(api, {"documents"}, ["card"])
        self.assertIn("stray: the page unshared above it is not shared with the integration", str(cm.exception))
        self.assertIn("pages/stray.md", copying.collect(api, set(), ["card"]).files)   # no exclusions: nothing to decide

    def test_an_unshared_ancestor_that_is_itself_excluded_excludes_what_hangs_under_it(self):
        api = world()
        api.pages.append(page("stray", "Shared alone", {"type": "page_id", "page_id": "unshared"}))
        got = copying.collect(api, {"unshared"}, ["card"])
        self.assertNotIn("pages/stray.md", got.files)
        self.assertEqual(got.excluded, 1)

    def test_a_row_the_search_has_not_listed_is_still_a_known_parent(self):
        api = world()
        api.pages = [p for p in api.pages if p["id"] != "row1"]                 # the search index lags behind the query
        api.pages.append(page("sub", "Under a row", {"type": "page_id", "page_id": "row1"}))
        got = copying.collect(api, {"documents"}, ["card"])
        self.assertIn("pages/sub.md", got.files)

    def test_a_parent_of_no_known_kind_stops_the_copy(self):
        api = world()
        api.pages.append(page("odd", "Odd", {"type": "page_id"}))
        with self.assertRaises(NotionError):
            copying.collect(api, {"documents"}, ["card"])

    def test_a_chain_longer_than_fifty_stops_the_copy_and_a_cycle_too(self):
        api = world()
        api.pages.append(page("deep", "Deep in columns", {"type": "block_id", "block_id": "b0"}))
        for i in range(60):                                              # 60 blocks above it
            api.parents[f"b{i}"] = {"type": "block_id", "block_id": f"b{i + 1}"}
        with self.assertRaises(NotionError) as cm:
            copying.collect(api, {"child"}, ["card"])
        self.assertIn("deep: more than 50 blocks above it", str(cm.exception))
        api = world()
        api.pages += [page("loopa", "A", {"type": "page_id", "page_id": "loopb"}),
                      page("loopb", "B", {"type": "page_id", "page_id": "loopa"})]   # each the other's parent
        with self.assertRaises(NotionError) as cm:
            copying.collect(api, {"child"}, ["card"])
        self.assertIn("more than 50 levels above it", str(cm.exception))

    def test_a_data_source_in_the_trash_is_skipped_before_its_place_is_asked(self):
        api = world()
        api.sources.append(dict(source("gone", "Old base", "gonedb", "trashedpage"), archived=True, in_trash=True))
        got = copying.collect(api, {"child"}, ["card"])                  # its page is not shared: no error
        self.assertNotIn("data/gone/_source.md", got.files)

    def test_an_exclusion_that_matches_nothing_leaves_a_whole_world_copied(self):
        got = copying.collect(world(), {"documents"}, ["card"])          # the base is never shared
        self.assertEqual((got.pages, got.rows, got.excluded), (2, 2, 0))

    def test_no_exclusions_means_no_parent_requests(self):
        api = world()
        api.pages.append(page("inner", "In a column", {"type": "block_id", "block_id": "missing"}))
        got = copying.collect(api, set(), ["card"])
        self.assertIn("pages/inner.md", got.files)
        self.assertEqual(got.notes, [])

    def test_a_card_number_is_cut_out_and_named_without_digits(self):
        api = world()
        api.blocks["child"] = [paragraph("b3", f"my card {CARD} exp 12/30")]
        api.pages[1]["properties"]["Name"]["title"] = rich(f"Card {CARD}")
        got = copying.collect(api, set(), ["card"])
        text = got.files["pages/child.md"]
        self.assertNotIn("4111", text)
        self.assertEqual(text.count(copying.MARKER), 3)     # the heading, the title property, the body
        self.assertEqual(len(got.redacted), 1)
        self.assertNotIn("4111", got.redacted[0])
        self.assertIn("pages/child.md: 3 card number(s) removed", got.redacted[0])

    def test_an_id_that_is_not_a_notion_id_never_becomes_a_path(self):
        api = world()
        api.pages.append(page("../../engine/checks/alarm", "Hostile", {"type": "page_id", "page_id": ROOT}))
        api.sources.append(source("../x", "Hostile source", "db2", ROOT))
        api.row_lists["../x"] = [page("ok", "Row", {"type": "data_source_id", "data_source_id": "../x"})]
        got = copying.collect(api, set(), ["card"])
        self.assertFalse(any(".." in p or p.startswith("/") for p in got.files))
        self.assertEqual(got.notes, ["NOTE a data source with an id that is not a Notion id was skipped",
                                     "NOTE a page with an id that is not a Notion id was skipped"])
        self.assertEqual(sorted(got.files)[-2:], ["pages/child.md", f"pages/{ROOT}.md"])

    def test_a_document_number_stays_in_the_copy(self):
        got = copying.collect(world(), set(), ["card"])
        self.assertIn("12345678", got.files[f"data/{DS}/row1.md"])

    def test_notion_down_raises_and_nothing_is_collected(self):
        api = world()
        api.down = True
        with self.assertRaises(Exception):
            copying.collect(api, set(), ["card"])


class Shrink(unittest.TestCase):
    def test_a_drop_over_the_share_is_an_alarm_and_a_smaller_one_is_not(self):
        self.assertIsNone(copying.shrink({"records": 10}, 9, 0.2))
        self.assertIsNone(copying.shrink({"records": 10}, 8, 0.2))       # exactly the share: not over it
        self.assertIn("fell from 10 to 7 records (30 %)", copying.shrink({"records": 10}, 7, 0.2))
        self.assertIsNone(copying.shrink(None, 0, 0.2))                 # the first run
        self.assertIsNone(copying.shrink({"records": 0}, 0, 0.2))
        self.assertIsNone(copying.shrink({"records": "ten"}, 1, 0.2))
        self.assertIsNone(copying.shrink({"records": 10}, 12, 0.2))     # growth

class HeldBases(unittest.TestCase):
    """A base's loss is held night after night until the cause is gone (Issue #18, item 43;
    owner, 09.11.2026: variant 2 with variant 3)."""

    BEFORE = {"bases": {"docs": {"rows": 10}, "big": {"rows": 990}}}
    SAME = {"docs": {"rows": 10}, "big": {"rows": 990}}
    GONE = ("the base data/docs (10 rows) is gone from the copy since 2026-11-09 — deleted in Notion, unshared from "
            "the copy's integration, or excluded in the config; held until it is back or notion.removed_bases "
            "records that she removed it")

    def held(self, previous, bases, excluded=frozenset(), removed=frozenset(), today="2026-11-09"):
        return copying.held_bases(previous, bases, set(excluded), set(removed), today)

    def night_after(self, held, bases):
        return {"bases": bases, "held": held}

    def test_a_base_gone_is_reported_and_held_with_the_day_it_went(self):
        lines, held = self.held(self.BEFORE, {"big": {"rows": 990}})
        self.assertEqual(lines, [self.GONE])
        self.assertEqual(held, {"docs": {"rows": 10, "since": "2026-11-09"}})

    def test_the_next_nights_report_it_in_the_same_words_until_its_rows_are_back(self):
        lines, held = self.held(self.BEFORE, {"big": {"rows": 990}})
        stamp = self.night_after(held, {"big": {"rows": 990}})
        for day in ("2026-11-10", "2026-11-11"):
            again, held = self.held(stamp, {"big": {"rows": 990}}, today=day)
            self.assertEqual(again, lines)                             # the same line: no new comment on the Issue
            stamp = self.night_after(held, {"big": {"rows": 990}})
        self.assertEqual(self.held(stamp, self.SAME), ([], {}))         # back with its rows: the cause is gone
        self.assertEqual(self.held(stamp, {**self.SAME, "docs": {"rows": 5}}), ([], {}))    # half of a big base is back
        lines, held = self.held(stamp, {**self.SAME, "docs": {"rows": 4}})                    # less than half: still held
        self.assertEqual(lines, ["the base data/docs lost more than half of its 10 rows since 2026-11-09; held until it "
                                 "has half of them back or notion.removed_bases records that she removed it"])
        self.assertEqual(held, {"docs": {"rows": 10, "since": "2026-11-09"}})
        lines, _ = self.held(stamp, {**self.SAME, "docs": {"rows": 0}})
        self.assertIn("the base data/docs is empty since 2026-11-09: 10 rows → 0", lines[0])

    def test_a_big_base_back_with_one_row_of_five_hundred_is_still_held(self):
        stamp = {"bases": {}, "held": {"docs": {"rows": 500, "since": "2026-11-01"}}}
        self.assertEqual(len(self.held(stamp, {"docs": {"rows": 1}})[0]), 1)
        self.assertEqual(self.held(stamp, {"docs": {"rows": 250}}), ([], {}))

    def test_a_big_base_that_halves_is_held_and_a_small_clean_up_is_not_an_alarm(self):
        lines, held = self.held(self.BEFORE, {**self.SAME, "docs": {"rows": 4}})
        self.assertIn("lost more than half of its 10 rows since 2026-11-09", lines[0])
        self.assertEqual(self.held(self.BEFORE, {**self.SAME, "docs": {"rows": 5}}), ([], {}))   # exactly half: not over it
        self.assertEqual(self.held(self.BEFORE, {**self.SAME, "big": {"rows": 500}}), ([], {}))  # 49 % of a big base: the total's N3 sees that
        small = {"bases": {"docs": {"rows": 3}}}
        self.assertEqual(self.held(small, {"docs": {"rows": 1}}), ([], {}))                    # a clean-up in a small base
        self.assertIn("data/docs is empty since 2026-11-09: 3 rows → 0", self.held(small, {"docs": {"rows": 0}})[0][0])
        self.assertIn("(1 row) is gone", self.held({"bases": {"docs": {"rows": 1}}}, {})[0][0])

    def test_her_recorded_decision_ends_that_loss_only_and_only_after_one_night(self):
        lines, held = self.held(self.BEFORE, {"big": {"rows": 990}}, removed={("docs", "2026-11-09")})
        self.assertEqual(len(lines), 1)                                # recorded before it went: reported all the same
        stamp = self.night_after(held, {"big": {"rows": 990}})
        self.assertEqual(self.held(stamp, {"big": {"rows": 990}}, removed={("docs", "2026-11-09")}), ([], {}))
        for stale in ({("docs", "2026-10-01")}, {("other", "2026-11-09")}):   # an older loss of it, another base
            self.assertEqual(len(self.held(stamp, {"big": {"rows": 990}}, removed=stale)[0]), 1, stale)

    def test_an_excluded_base_is_reported_the_night_it_goes_and_not_held(self):
        lines, held = self.held(self.BEFORE, {"big": {"rows": 990}}, excluded={"docs"})
        self.assertEqual((len(lines), held), (1, {}))
        stamp = {"bases": {"big": {"rows": 990}}, "held": {"docs": {"rows": 10, "since": "2026-11-01"}}}
        self.assertEqual(self.held(stamp, {"big": {"rows": 990}}, excluded={"docs"}), ([], {}))   # excluded later: let go

    def test_nothing_before_and_junk_in_the_stamp_hold_nothing(self):
        self.assertEqual(self.held(None, self.SAME), ([], {}))                                    # the first run
        self.assertEqual(self.held({"records": 10}, {}), ([], {}))                                # a stamp from before this check
        junk = {"held": {"a": "ten", "b": {"rows": 0, "since": "2026-11-01"}, "c": {"rows": 2, "since": "soon"},
                         "d": {"rows": True, "since": "2026-11-01"}},
                "bases": {"x": {"rows": 0}, "y": "ten", "z": {"title": "Old", "rows": 2}}}
        self.assertEqual(self.held(junk, {"z": {"rows": 2}}), ([], {}))     # junk, and a stamp from the version that kept titles
        self.assertEqual(self.held(self.BEFORE, {**self.SAME, "new": {"rows": 2}}), ([], {}))   # a new base is not a loss

    def test_decisions_are_read_as_id_and_day(self):
        dashed = f"{DS[:8]}-{DS[8:]}"
        self.assertEqual(copying.removed_decisions([f"{dashed}@2026-11-09", "docs@2026-11-10", "docs", "docs@soon",
                                                    7, "@2026-11-09", "-@2026-11-09"]),
                         {(DS, "2026-11-09"), ("docs", "2026-11-10")})
        self.assertEqual(copying.removed_decisions("docs@2026-11-09"), set())

    def test_only_a_base_the_config_names_is_an_excluded_base(self):
        dashed = f"{DB[:8]}-{DB[8:]}"
        for named in ({DS}, {dashed}):                                 # the data source, or its database
            self.assertEqual(copying.collect(world(), named, ["card"]).excluded_bases, {DS}, named)
        under = copying.collect(world(), {ROOT}, ["card"])            # under an excluded page: held like any loss
        self.assertEqual((under.excluded, under.excluded_bases), (4, set()))


class Write(unittest.TestCase):
    def test_the_old_copy_goes_the_new_lands_and_the_stamp_is_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "pages").mkdir()
            (wt / "pages" / "stale.md").write_text("old", encoding="utf-8")
            got = copying.Copy(files={"pages/a.md": "A\n", "data/x/_source.md": "S\n"}, pages=1, sources=1)
            copying.write(got, wt, {"records": 1, "run": "t"})
            self.assertFalse((wt / "pages" / "stale.md").exists())
            self.assertEqual((wt / "pages" / "a.md").read_text(encoding="utf-8"), "A\n")
            self.assertEqual((wt / "copy.json").read_text(encoding="utf-8"), '{\n  "records": 1,\n  "run": "t"\n}\n')
            self.assertEqual(copying.read_stamp(wt), {"records": 1, "run": "t"})
            self.assertIn("Never merged", (wt / "README.md").read_text(encoding="utf-8"))

    def test_a_broken_stamp_reads_as_empty_not_as_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "copy.json").write_text("{", encoding="utf-8")
            self.assertEqual(copying.read_stamp(Path(tmp)), {})
            self.assertIsNone(copying.read_stamp(Path(tmp) / "nowhere"))


class Run(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = Path(self.tmp.name) / "wt"
        self.wt.mkdir()
        self.report = Path(self.tmp.name) / "report.json"

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def call(self, api, main=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run.main(["--repo", str(self.repo.path), "--main", main or self.repo.base,
                             "--worktree", str(self.wt), "--report", str(self.report)],
                            api=api, now=datetime(2026, 10, 24, 1, 11, tzinfo=timezone.utc))
        return code, out.getvalue(), json.loads(self.report.read_text(encoding="utf-8"))

    def test_a_clean_night_writes_the_copy_and_the_stamp(self):
        code, out, report = self.call(world())
        self.assertEqual(code, 0, out)
        self.assertEqual(report, {"N3": [], "N6": [], "error": [], "skipped": []})
        stamp = json.loads((self.wt / "copy.json").read_text(encoding="utf-8"))
        self.assertEqual((stamp["records"], stamp["pages"], stamp["rows"], stamp["release"], stamp["run"]),
                         (4, 2, 2, "v0.1.0", "2026-10-24T01:11:00Z"))
        self.assertIn("COPY records=4 pages=2 rows=2 sources=1 excluded=0", out)
        self.assertIn("SUMMARY alarm=0 ran=N3,N6", out)

    def test_a_shrink_is_an_alarm_but_the_copy_is_still_written(self):
        self.call(world())
        api = world()
        api.row_lists[DS] = []
        api.pages = api.pages[:1]
        code, out, report = self.call(api)
        self.assertEqual(code, 1)
        self.assertIn("fell from 4 to 1 records (75 %)", report["N3"][0])
        self.assertEqual(json.loads((self.wt / "copy.json").read_text(encoding="utf-8"))["records"], 1)

    def test_the_person_may_tighten_the_share_but_not_loosen_it(self):
        self.repo.checkout(self.repo.base)
        tight = self.repo.commit({"user/config.toml": VALID_CONFIG + b'\n[notion]\nshrink_alarm = 0.05\n'})
        self.assertEqual(run.settings(self.repo.path, tight)[1], 0.05)
        self.repo.checkout(self.repo.base)
        loose = self.repo.commit({"user/config.toml": VALID_CONFIG + b'\n[notion]\nshrink_alarm = 0.9\n'})
        self.assertEqual(run.settings(self.repo.path, loose)[1], 0.2)
        self.assertEqual(run.settings(self.repo.path, self.repo.base), (set(), 0.2, ["card"], set()))

    def test_the_bases_she_removed_come_from_the_config_in_any_spelling(self):
        self.repo.checkout(self.repo.base)
        rev = self.repo.commit({"user/config.toml": VALID_CONFIG + f'\n[notion]\nremoved_bases = ["{DS[:8]}-{DS[8:]}@2026-11-09"]\n'.encode()})
        self.assertEqual(run.settings(self.repo.path, rev)[3], {(DS, "2026-11-09")})
        self.repo.checkout(self.repo.base)
        bad = self.repo.commit({"user/config.toml": VALID_CONFIG + b'\n[notion]\nremoved_bases = [7]\n'})
        with self.assertRaises(ValueError):                    # a number in the list is a broken config, not «nothing»
            run.settings(self.repo.path, bad)

    def test_exclusions_come_from_the_config_in_any_spelling(self):
        self.repo.checkout(self.repo.base)
        rev = self.repo.commit({"user/config.toml": VALID_CONFIG + f'\n[notion]\nbackup_exclude = ["{DS[:8]}-{DS[8:]}", "CHILD"]\n'.encode()})
        self.assertEqual(run.settings(self.repo.path, rev)[0], {DS, "child"})

    def test_a_card_number_is_an_alarm_and_the_copy_holds_the_marker(self):
        api = world()
        api.blocks["child"] = [paragraph("b3", f"card {CARD}")]
        code, out, report = self.call(api)
        self.assertEqual(code, 1)
        self.assertEqual(len(report["N6"]), 1)
        self.assertNotIn("4111", (self.wt / "pages" / "child.md").read_text(encoding="utf-8"))
        self.assertNotIn("4111", out)

    def test_notion_down_writes_nothing_and_is_an_error(self):
        self.call(world())
        api = world()
        api.down = True
        code, out, report = self.call(api)
        self.assertEqual(code, 2)
        self.assertIn("the copy could not be made: NotionError", report["error"][0])
        self.assertEqual(json.loads((self.wt / "copy.json").read_text(encoding="utf-8"))["records"], 4)   # untouched
        self.assertTrue((self.wt / "pages" / "child.md").exists())

    def failing_write(self, rel: str):
        """Path.write_text that fails, once, on this one file of the worktree (an I/O error);
        every other write goes through. A full disk is `test_a_full_disk_…` below."""
        target, real, fired = self.wt / rel, Path.write_text, []

        def write_text(path, *args, **kwargs):
            if path == target and not fired:
                fired.append(rel)
                raise OSError(errno.EIO, "Input/output error", str(path))
            return real(path, *args, **kwargs)
        return mock.patch.object(Path, "write_text", write_text)

    def test_a_write_that_fails_is_the_copys_error_not_a_finding(self):
        # Issue #18, item 42: `write` stood outside the try and the crash exited 1 —
        # «N3 or N6 found something» — and the workflow committed the half tree.
        self.call(world())
        for rel in ("pages/child.md", "copy.json"):          # a page; the stamp, after every page
            with self.subTest(rel=rel), self.failing_write(rel):
                code, out, report = self.call(world())
                self.assertEqual(code, 2)
                self.assertEqual(report["error"], [f"the copy could not be made: OSError: [Errno {errno.EIO}] "
                                                   f"Input/output error: '{self.wt / rel}'"])
                self.assertEqual((report["N3"], report["N6"]), ([], []))   # this night judged neither
                self.assertIn("ERROR the copy could not be made", out)

    def test_a_report_that_cannot_be_written_is_no_report_and_exit_2(self):
        # The report is the night's last write, in one step: a failed write leaves no file,
        # not an empty one (an empty one would pass for «finished» — the pre-check of this PR).
        real = Path.write_text

        def write_text(path, *args, **kwargs):
            if path.name.endswith(".part"):
                real(path, "")                                       # emptied, as a full disk does
                raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return real(path, *args, **kwargs)
        out = io.StringIO()
        with mock.patch.object(Path, "write_text", write_text), contextlib.redirect_stdout(out):
            code = run.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--worktree", str(self.wt),
                             "--report", str(self.report)], api=world(), now=datetime(2026, 10, 24, 1, 11, tzinfo=timezone.utc))
        self.assertEqual(code, 2)
        self.assertEqual(sorted(p.name for p in self.report.parent.iterdir()), ["wt"])   # no report, no .part
        self.assertIn("ERROR the report could not be written: OSError", out.getvalue())

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_FSIZE as the full disk")
    def test_a_full_disk_leaves_no_report_and_exits_2(self):
        # A real full disk, not a patch: in a child process no file may grow past 0 bytes, so
        # every write — the copy's and the report's — is emptied, then fails (EFBIG).
        self.call(world())                                           # last night's copy
        self.report.unlink()
        driver = ("import sys; from datetime import datetime, timezone; from engine.backup import run; "
                  "from engine.backup.tests.test_copy import world; "
                  "sys.exit(run.main(sys.argv[1:], api=world(), now=datetime(2026, 10, 25, 1, 11, tzinfo=timezone.utc)))")

        def no_room():
            import resource
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        done = subprocess.run([sys.executable, "-B", "-c", driver, "--repo", str(self.repo.path), "--main", self.repo.base,
                               "--worktree", str(self.wt), "--report", str(self.report)],
                              cwd=REPO_ROOT, capture_output=True, text=True, preexec_fn=no_room, timeout=60)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("ERROR the copy could not be made: OSError", done.stdout)
        self.assertEqual(sorted(p.name for p in self.report.parent.iterdir()), ["wt"])   # no report, no .part

    def test_a_text_utf8_cannot_hold_is_the_copys_error(self):
        api = world()
        api.blocks["child"] = [paragraph("b3", "half an emoji \ud83d")]      # a lone surrogate
        code, out, report = self.call(api)
        self.assertEqual(code, 2)
        self.assertIn("the copy could not be made: UnicodeEncodeError", report["error"][0])

    def test_a_broken_engine_lock_is_the_copys_error(self):
        self.repo.checkout(self.repo.base)
        rev = self.repo.commit({"user/engine.lock": b"release = \n"})
        code, out, report = self.call(world(), main=rev)
        self.assertEqual(code, 2)
        self.assertIn("the copy could not be made: TOMLDecodeError", report["error"][0])
        self.assertFalse((self.wt / "copy.json").exists())                # nothing written

    def test_a_chain_that_cannot_be_followed_is_the_nights_error_through_main(self):
        self.call(world())
        self.repo.checkout(self.repo.base)
        rev = self.repo.commit({"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["child"]\n'})
        api = world()
        api.pages.append(page("inner", "In a column", {"type": "block_id", "block_id": "missing"}))
        code, out, report = self.call(api, main=rev)
        self.assertEqual(code, 2)
        self.assertIn("the copy could not be made: NotionError: inner: a block above it could not be read", report["error"][0])
        self.assertEqual(json.loads((self.wt / "copy.json").read_text(encoding="utf-8"))["records"], 4)   # untouched

    def test_a_broken_or_missing_config_stops_the_run_and_writes_nothing(self):
        # Issue #18, item 46: a config the copy cannot read whole was read as «exclude nothing».
        self.call(world())
        for files in ({"user/config.toml": b"[notion\n"}, {"user/config.toml": None},
                      {"user/config.toml": VALID_CONFIG + b'\n[notion]\nbackup_exlude = ["child"]\n'}):   # a typo in a key
            with self.subTest(files):
                self.repo.checkout(self.repo.base)
                rev = self.repo.commit(files)
                code, out, report = self.call(world(), main=rev)
                self.assertEqual(code, 2)
                self.assertIn("the copy could not be made: ValueError: the settings are not usable", report["error"][0])
                self.assertEqual(json.loads((self.wt / "copy.json").read_text(encoding="utf-8"))["records"], 4)   # untouched

    def test_a_missing_floor_is_an_error(self):
        self.repo.checkout(self.repo.base)
        rev = self.repo.commit({"engine/defaults.toml": b"[floors]\nchat_field_limit_max = 1\n"})
        code, out, report = self.call(world(), main=rev)
        self.assertEqual(code, 2)
        self.assertIn("backup_shrink_alarm_max", report["error"][0])


if __name__ == "__main__":
    unittest.main()
