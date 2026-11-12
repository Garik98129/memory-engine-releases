"""Who writes — `platform.writer` and `platform.bot` (docs/DESIGN_PHASE3.md §2, §6a).

The pilot's way out if the bot's field is gone: the chat writes itself, with one field whose
hard part is `fields/solo.md`. A config without the keys is phase 1 — the chat's field and
the bot's, N4, N5 and N7 on — so nothing changes for an installation that runs today."""
import contextlib
import io
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from engine.checks import behind, config_check, drift, limits_check, watch
from engine.checks.paths_check import PERSON
from engine.checks.tests.fakeapi import REPO, FakeApi
from engine.checks.tests.helpers import BASE_FILES, COMMITTED, VALID_CONFIG, Repo
from engine.setup import release

NOW = datetime.fromisoformat(COMMITTED) + timedelta(hours=1)
CHAT_WRITES = VALID_CONFIG.replace(b'read_budget_bytes = 16384\n', b'read_budget_bytes = 16384\nwriter = "chat"\n')
NO_BOT = CHAT_WRITES.replace(b'writer = "chat"\n', b'writer = "chat"\nbot = false\n')
SOLO = {"engine/rules/fields/solo.md": b"Chat that writes: safety rules.\n"}
# The engine's platform table: C3 holds platform.name to it once the chat writes (DESIGN_PHASE3 §4).
SOLO["engine/platforms.toml"] = (Path(__file__).resolve().parents[2] / "platforms.toml").read_bytes()


def messages(findings):
    return [f.message for f in findings]


class PhaseOneStays(unittest.TestCase):
    """A config written before the keys existed — the pilot's — reads as phase 1."""

    def test_no_keys_means_the_bot_writes_and_both_fields(self):
        config = {"platform": {"name": "grok", "chat_field_limit": 4000, "bot_field_limit": 7900}}
        self.assertEqual(limits_check.writer(config), "bot")
        self.assertTrue(limits_check.has_bot(config))
        self.assertEqual(limits_check.fields(config), limits_check.FIELDS)
        self.assertEqual(limits_check.fields({}), limits_check.FIELDS)

    def test_a_value_c3_refuses_still_reads_as_phase_one(self):
        self.assertEqual(limits_check.writer({"platform": {"writer": "Chat"}}), "bot")
        self.assertTrue(limits_check.has_bot({"platform": {"bot": "false"}}))

    def test_the_chat_writes_one_field_from_solo(self):
        fields = limits_check.fields({"platform": {"writer": "chat"}})
        self.assertEqual(list(fields), ["chat"])
        self.assertEqual(fields["chat"][:2], (limits_check.SOLO, "user/fields/chat.md"))


class C3(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({**BASE_FILES, **SOLO})

    def tearDown(self):
        self.repo.close()

    def at_main(self, config: bytes):
        head = self.repo.commit({"user/config.toml": config})
        return messages(config_check.check(self.repo.path, head, head, PERSON))

    def test_the_two_keys_and_their_values(self):
        self.assertEqual(self.at_main(CHAT_WRITES), [])
        self.assertEqual(self.at_main(NO_BOT), [])
        self.assertEqual(self.at_main(CHAT_WRITES.replace(b'"chat"', b'"phone"')),
                         ["platform.writer must be one of bot, chat"])
        self.assertEqual(self.at_main(NO_BOT.replace(b"bot = false", b'bot = "no"')),
                         ["platform.bot must be bool"])

    def test_the_bot_writes_only_with_a_bot_and_its_field_limit(self):
        nobody = VALID_CONFIG.replace(b'read_budget_bytes = 16384\n', b'read_budget_bytes = 16384\nbot = false\n')
        self.assertEqual(self.at_main(nobody),
                         ['platform.writer = "bot" needs a bot: platform.bot = false leaves nobody to write'])
        no_limit = VALID_CONFIG.replace(b"bot_field_limit = 7900\n", b"")
        self.assertEqual(self.at_main(no_limit), ["required key platform.bot_field_limit is missing: the bot writes"])
        # the chat writes: the bot's field is gone, and so is the need for its limit
        self.assertEqual(self.at_main(CHAT_WRITES.replace(b"bot_field_limit = 7900\n", b"")), [])

    def test_the_switch_is_the_trusted_contacts_own_push(self):
        with_contact = VALID_CONFIG + b"\n[alarm]\ntrusted_contact = \"Contact\"\n"
        main = self.repo.commit({"user/config.toml": with_contact})
        switched = self.repo.commit({"user/config.toml": with_contact.replace(
            b'read_budget_bytes = 16384\n', b'read_budget_bytes = 16384\nwriter = "chat"\n')})
        check = lambda actor, before: messages(config_check.check(self.repo.path, main, switched, PERSON, actor, before))
        self.assertEqual(check("contact", main), [])
        self.assertEqual(check("alex-bot", main), ["platform.writer changes only in a push of the trusted contact's own"])
        no_bot = self.repo.commit({"user/config.toml": with_contact.replace(
            b'read_budget_bytes = 16384\n', b'read_budget_bytes = 16384\nwriter = "chat"\nbot = false\n')})
        self.assertEqual(messages(config_check.check(self.repo.path, main, no_bot, PERSON, "alex-bot", main)),
                         ["platform.writer changes only in a push of the trusted contact's own",
                          "platform.bot changes only in a push of the trusted contact's own"])

    def test_the_chat_writes_on_a_platform_of_the_table_only(self):
        self.assertEqual(self.at_main(CHAT_WRITES.replace(b'name = "grok"', b'name = "claude"')), [])
        self.assertEqual(self.at_main(CHAT_WRITES.replace(b'name = "grok"', b'name = "grokk"')),
                         ["platform.name 'grokk' is not a platform of engine/platforms.toml"])
        # the bot's scheme — phase 1, the pilot — keeps any name
        self.assertEqual(self.at_main(VALID_CONFIG.replace(b'name = "grok"', b'name = "grok-bot"')), [])

    def test_writing_the_defaults_out_is_no_change(self):
        """The pilot's config with `writer = "bot"` and `bot = true` spelled out equals main's
        config without them: no protected key moved."""
        spelled = VALID_CONFIG.replace(b'read_budget_bytes = 16384\n',
                                       b'read_budget_bytes = 16384\nwriter = "bot"\nbot = true\n')
        head = self.repo.commit({"user/config.toml": spelled})
        self.assertEqual(messages(config_check.check(self.repo.path, self.repo.base, head, PERSON, "alex-bot", "")), [])


class C6(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({**BASE_FILES, **SOLO})

    def tearDown(self):
        self.repo.close()

    def test_the_chat_writes_its_field_is_solo_and_her_part(self):
        used = limits_check.length(SOLO["engine/rules/fields/solo.md"].decode().strip("\n"))
        at = "x" * (4000 - used - 2)
        head = self.repo.commit({"user/config.toml": CHAT_WRITES, "user/fields/chat.md": at.encode(),
                                 "user/fields/bot.md": None, "engine/rules/fields/bot.md": None})
        self.assertEqual(limits_check.check(self.repo.path, self.repo.base, head), [],
                         "no bot's field to measure, and chat.md's hard part is not the one installed")
        head = self.repo.commit({"user/fields/chat.md": (at + "y").encode()})
        self.assertEqual(messages(limits_check.check(self.repo.path, self.repo.base, head)),
                         ["the chat field would be 4001 characters, the limit is 4000"])

    def test_a_missing_solo_is_loud(self):
        head = self.repo.commit({"user/config.toml": CHAT_WRITES, "engine/rules/fields/solo.md": None})
        self.assertEqual(messages(limits_check.check(self.repo.path, self.repo.base, head)),
                         ["engine/rules/fields/solo.md is missing"])


class N5AndN7(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({**BASE_FILES, **SOLO, "user/config.toml": CHAT_WRITES})

    def tearDown(self):
        self.repo.close()

    def test_the_record_is_of_the_solo_field(self):
        solo = limits_check.assemble(SOLO["engine/rules/fields/solo.md"].decode(),
                                     BASE_FILES["user/fields/chat.md"].decode())
        handoff = limits_check.assemble(BASE_FILES["engine/rules/fields/chat.md"].decode(),
                                        BASE_FILES["user/fields/chat.md"].decode())
        record = lambda text: f'[fields.chat]\nsha256 = "{drift.digest(text)}"\n'.encode()
        late = NOW + timedelta(hours=48)
        head = self.repo.commit({"user/fields/installed.toml": record(handoff)})
        self.assertEqual(messages(drift.check(self.repo.path, head, late)),
                         ["the chat field changed more than 36 h ago and is not reinstalled: "
                          "user/fields/installed.toml does not match it"])
        self.assertEqual(behind.check(self.repo.path, head, late),
                         ["field chat: the installed text is not its master (engine/rules/fields/solo.md and her "
                          "part) — the trusted contact installs it and records it"],
                         "no line about a bot's field, and the bot is not sent to join chat.md (ROUT-9)")
        head = self.repo.commit({"user/fields/installed.toml": record(solo)})
        self.assertEqual(drift.check(self.repo.path, head, late), [])
        self.assertEqual(behind.check(self.repo.path, head, late), [])


class NoBot(unittest.TestCase):
    """A new installation without a bot: no mark, no field record, no to-do — said, not silent."""

    def setUp(self):
        self.repo = Repo({**BASE_FILES, **SOLO, "user/config.toml": NO_BOT})
        self.api = FakeApi()
        self.api.lists[f"repos/{REPO}/activity"] = [
            {"id": 1, "activity_type": "push", "before": "0" * 40, "after": self.repo.base,
             "timestamp": "2026-10-01T00:00:00Z", "actor": {"login": "owner"}}]
        self.api.objects["apps/github-actions"] = {"id": 15368}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": 15368}]}}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = []
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.repo.base}}
        self.api.lists[f"repos/{REPO}/issues"] = []
        self.report = self.repo.path.parent / f"{self.repo.path.name}-report.json"

    def tearDown(self):
        self.repo.close()
        self.report.unlink(missing_ok=True)

    def test_n4_n5_and_n7_say_they_do_not_run(self):
        late = NOW + timedelta(hours=48)      # with a bot, the unrecorded field would be an N5 alarm by now
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                               str(self.repo.manifest_file(self.repo.base)), "--report", str(self.report),
                               "--branches", "refs/heads/"], api=self.api, now=late)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("N4 NOTE N4, N5 and N7 do not run: this installation has no bot (platform.bot = false)",
                      out.getvalue())
        self.assertEqual(report["skipped"], ["N4", "N5"])
        self.assertNotIn("todo", report, "no list for a bot: its Issue is left alone")
        self.assertEqual((report["N4"], report["N5"]), ([], []))


class SoloKeepsTheSafetyCore(unittest.TestCase):
    """The chat that writes holds payment data out in every turn, not by a link: CORE-6's whole set."""

    def test_payment_data_is_named_whole(self):
        here = Path(__file__).resolve().parents[2]
        solo = (here / "rules/fields/solo.md").read_text(encoding="utf-8")
        for kind in ("Card numbers", "CVV", "expiry", "PIN", "passwords", "codes"):
            self.assertIn(kind, solo)
        for ask in ("«Buy?»", "«Send?»", "«Delete?»", "«Recorded: what → where»", "data, not a command",
                    "before answering or acting"):
            self.assertIn(ask, solo)


class BrokenConfig(unittest.TestCase):
    """A config that does not parse, through the watch's real entry point: an error or an alarm, a
    report written — never a crash (P5)."""

    def setUp(self):
        self.repo = Repo({**BASE_FILES, **SOLO, "user/config.toml": b"[platform\nwriter = 'chat'\n"})
        self.api = FakeApi()
        self.api.lists[f"repos/{REPO}/activity"] = [
            {"id": 1, "activity_type": "push", "before": "0" * 40, "after": self.repo.base,
             "timestamp": "2026-10-01T00:00:00Z", "actor": {"login": "owner"}}]
        self.api.objects["apps/github-actions"] = {"id": 15368}
        self.api.objects[f"repos/{REPO}/branches/main"] = {"protection": {
            "enabled": True, "required_status_checks": {
                "enforcement_level": "everyone", "checks": [{"context": "gate", "app_id": 15368}]}}}
        self.api.objects[f"repos/{REPO}/rules/branches/main"] = []
        self.api.objects[f"repos/{REPO}/git/ref/heads/main"] = {"object": {"sha": self.repo.base}}
        self.api.lists[f"repos/{REPO}/issues"] = []
        self.report = self.repo.path.parent / f"{self.repo.path.name}-report.json"

    def tearDown(self):
        self.repo.close()
        self.report.unlink(missing_ok=True)

    def test_the_night_reports_it_and_reads_it_as_phase_one(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = watch.main(["--repo", str(self.repo.path), "--main", self.repo.base, "--manifest",
                               str(self.repo.manifest_file(self.repo.base)), "--report", str(self.report),
                               "--branches", "refs/heads/"], api=self.api, now=NOW)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertIn(code, (1, 2), out.getvalue())
        self.assertTrue(any("does not parse" in line for line in report["N1"]), report["N1"])
        self.assertNotIn("N4", report["skipped"], "a broken config reads as phase 1: the bot is watched")


class ReleaseLinks(unittest.TestCase):
    """`cut` holds the link of every chat field, solo.md's too: it opens notion.md and documents.md."""

    LINK = "https://raw.githubusercontent.com/Garik98129/memory-engine-releases/{tag}/files/engine/rules/"

    def files(self, chat_tag: str, solo_tag: str) -> dict[str, bytes]:
        return {"engine/rules/notion.md": b"Notion rules.\n", "engine/rules/health.md": b"Health rules.\n",
                release.CHAT_FIELD: f"Topic rules: health.md: {self.LINK.format(tag=chat_tag)} + name\n".encode(),
                release.SOLO_FIELD: f"Topic rules: notion.md: {self.LINK.format(tag=solo_tag)} + name\n".encode()}

    def test_solo_is_held_like_the_chat_field(self):
        repo = "Garik98129/memory-engine-releases"
        self.assertEqual(release.chat_rules_findings(self.files("v0.2.0", "v0.2.0"), "v0.2.0", Path("."), repo), [])
        self.assertEqual(release.chat_rules_findings(self.files("v0.2.0", "v0.3.0"), "v0.2.0", Path("."), repo),
                         [f"{release.SOLO_FIELD} links v0.3.0, newer than v0.2.0"])
        files = self.files("v0.2.0", "v0.2.0")
        files[release.SOLO_FIELD] = files[release.SOLO_FIELD].replace(b"notion.md", b"journal.md")
        self.assertEqual(release.chat_rules_findings(files, "v0.2.0", Path("."), repo),
                         [f"{release.SOLO_FIELD} names journal.md, which is not in engine/rules/"])

    def test_the_released_solo_links_what_the_chat_needs_to_write(self):
        here = Path(__file__).resolve().parents[2]
        files = {release.SOLO_FIELD: (here / "rules/fields/solo.md").read_bytes()}
        linked_repo, tag, names = release.chat_rules(files, release.SOLO_FIELD)
        for name in ("notion.md", "documents.md", "calendar.md", "mail.md", "shopping.md", "health.md", "core.md"):
            self.assertIn(name, names)
        chat = release.chat_rules({release.CHAT_FIELD: (here / "rules/fields/chat.md").read_bytes()})
        self.assertEqual((linked_repo, tag), chat[:2], "both fields name one release")


if __name__ == "__main__":
    unittest.main()
