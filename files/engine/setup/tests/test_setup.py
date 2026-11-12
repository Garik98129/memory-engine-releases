"""`setup` (docs/DESIGN_PHASE3.md §5, step 2): what a new installation writes for the person,
and install.md, the rules of her first conversation."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from engine.checks import config_check, limits_check
from engine.checks.paths_check import PERSON
from engine.checks.tests.helpers import BASE_FILES, Repo
from engine.setup import setup

HERE = Path(__file__).resolve().parents[2]
CHOICES = dict(platform="grok", language="ru", country="IL", timezone="Asia/Jerusalem", name="Anna",
               gender="female", emergency="101")
LOCK = b'release = "v0.2.0"\n'
LINK = "https://raw.githubusercontent.com/Garik98129/memory-engine-releases/v0.2.0/files/engine/rules/install.md"


class Build(unittest.TestCase):
    def test_what_she_gets_passes_c3_and_c6_at_main(self):
        """Her config and part as setup writes them, judged on a tree like hers with main = head.
        As a pull request against a template main with no config, C3 would still ask for the
        protected keys: that path is PR 3's (DESIGN_PHASE3 §5, «Still to design»)."""
        files = setup.build(**CHOICES)
        tree = {**BASE_FILES, "engine/rules/fields/solo.md": (HERE / "rules/fields/solo.md").read_bytes(),
                "engine/platforms.toml": (HERE / "platforms.toml").read_bytes(),
                setup.CONFIG: files[setup.CONFIG].encode(), setup.PART: files[setup.PART].encode()}
        del tree["user/fields/bot.md"]          # a new installation has no bot's part
        repo = Repo(tree)
        try:
            self.assertEqual(config_check.check(repo.path, repo.base, repo.base, PERSON), [])
            self.assertEqual(limits_check.check(repo.path, repo.base, repo.base), [])
        finally:
            repo.close()
        config, part = files[setup.CONFIG], files[setup.PART]
        for line in ('writer = "chat"', "bot = false", 'protection = "none"', 'channel = "stable"', "backup = false"):
            self.assertIn(line, config)
        for key in setup.KEYS:
            self.assertIn(f"«· {key}»", part)
        self.assertIn("Her emergency number: 101", part)
        self.assertIn("She is a woman", part)
        self.assertIn("Her platform: Grok; needs plan: any;", part, "INSTALL-5 reads what works here, not the Issue")
        self.assertLessEqual(limits_check.length(setup.field_text(part)), 4000)

    def test_a_man_is_told_apart(self):
        part = setup.build(**{**CHOICES, "name": "Igal", "gender": "male"})[setup.PART]
        self.assertIn("He is a man: masculine forms for him", part)

    def test_the_field_limit_never_goes_above_the_floor(self):
        files = setup.build(**{**CHOICES, "platform": "chatgpt"})
        self.assertIn("chat_field_limit = 4000", files[setup.CONFIG], "5 000 in the table, 4 000 the floor")

    def test_what_it_refuses(self):
        for change, why in ((dict(platform="gemini"), "not supported yet"),
                            (dict(platform="copilot"), "is not one of"),
                            (dict(language="Russian"), "language"),
                            (dict(country="ISR"), "country"),
                            (dict(timezone="Jerusalem"), "time zone"),
                            (dict(timezone="Factory"), "time zone"),
                            (dict(name=""), "name"),
                            (dict(name='An"na'), "name"),
                            (dict(name="An\\na"), "name"),
                            (dict(name="An\na"), "name"),
                            (dict(name="An\rna"), "name"),
                            (dict(name="Anna 0501234567"), "name"),
                            (dict(name="A" * 61), "name"),
                            (dict(name="M\u0430rina"), "look alike"),       # a Cyrillic a among Latin letters (C5)
                            (dict(gender="other"), "gender"),
                            (dict(emergency="1-0-1"), "emergency"),
                            (dict(emergency="00"), "emergency"),
                            (dict(emergency="000000"), "emergency"),
                            (dict(emergency="1234567"), "emergency")):
            with self.subTest(change=change):
                with self.assertRaisesRegex(setup.SetupError, why):
                    setup.build(**{**CHOICES, **change})


class InstallRules(unittest.TestCase):
    """install.md is the one text that makes her Notion: its safety lines are held here."""

    TEXT = (HERE / "rules/install.md").read_text(encoding="utf-8")

    def test_every_key_it_makes_is_one_her_part_names(self):
        for key in setup.KEYS:
            self.assertIn(f"· {key}»", self.TEXT)

    def test_its_safety_lines(self):
        for line in ("ask one «yes?»", "Her yes covers the missing items of INSTALL-2 and nothing else",
                     "Never move, rename or delete a page of hers",
                     "never make an item that is already there", "search her\n  Notion for each key",
                     "read her the emergency number", "one row «Signature» with an empty file",
                     "never delete either yourself", "Set up no scheduled task at installation",
                     "never a promise the platform\n  cannot keep", "top level of her workspace"):
            self.assertIn(line, self.TEXT)


class TheIssue(unittest.TestCase):
    def setUp(self):
        self.table = setup.platforms()

    def text(self, platform: str) -> str:
        files = setup.build(**{**CHOICES, "platform": platform})
        return setup.issue(self.table[platform], setup.field_text(files[setup.PART]), LINK)

    def test_it_carries_the_field_the_link_and_the_plan(self):
        claude = self.text("claude")
        self.assertIn(setup.field_text(setup.build(**{**CHOICES, "platform": "claude"})[setup.PART]), claude)
        self.assertIn(f"Open {LINK} and follow it", claude)
        self.assertIn("You need the Pro plan.", claude)
        self.assertIn("a new project, its instructions", claude)
        chatgpt = self.text("chatgpt")
        self.assertIn("You need the Plus plan.", chatgpt, "on Free and Go the field cuts her part off")
        self.assertIn("not known yet", chatgpt)
        grok = self.text("grok")
        self.assertNotIn("You need the", grok)
        self.assertIn("not confirmed yet", grok, "Automations on a free account: a probe (§8)")
        self.assertIn("No daily task is set up at installation", grok)


class Main(unittest.TestCase):
    def run_main(self, repo: Path, issue: Path, **change) -> tuple[int, str]:
        argv = ["--repo", str(repo), "--issue", str(issue)]
        for k, v in {**CHOICES, **change}.items():
            argv += [f"--{k}", v]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = setup.main(argv)
        return code, out.getvalue()

    def template(self, tmp: str, lock: bytes | None = LOCK) -> Path:
        repo = Path(tmp) / "repo"
        (repo / "user").mkdir(parents=True)
        if lock is not None:
            (repo / "user/engine.lock").write_bytes(lock)
        return repo

    def test_it_writes_once_and_refuses_a_second_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, issue = self.template(tmp), Path(tmp) / "issue.md"
            code, out = self.run_main(repo, issue)
            self.assertEqual(code, 0, out)
            self.assertTrue((repo / setup.CONFIG).is_file() and (repo / setup.PART).is_file())
            text = issue.read_text(encoding="utf-8")
            self.assertIn("Your assistant's instructions — Grok", text)
            self.assertIn(LINK, text, "install.md at the release her repository holds")
            code, out = self.run_main(repo, issue, name="Other")
            self.assertEqual(code, 2)
            self.assertIn("first installation only", out)
            self.assertIn("Anna", (repo / setup.PART).read_text(encoding="utf-8"), "nothing overwritten")

    def test_a_refused_choice_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, issue = self.template(tmp), Path(tmp) / "issue.md"
            code, _ = self.run_main(repo, issue, platform="gemini")
            self.assertEqual(code, 2)
            self.assertFalse((repo / setup.CONFIG).exists() or issue.exists())

    def test_no_release_in_the_lock_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, issue = self.template(tmp, lock=None), Path(tmp) / "issue.md"
            code, out = self.run_main(repo, issue)
            self.assertEqual(code, 2)
            self.assertIn("names no release", out)
            self.assertFalse((repo / setup.CONFIG).exists() or issue.exists())

    def test_an_issue_that_cannot_be_written_leaves_her_part_unwritten(self):
        """A rerun must not be refused for a half installation."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.template(tmp)
            code, out = self.run_main(repo, Path(tmp) / "no-such-folder" / "issue.md")
            self.assertEqual(code, 2)
            self.assertIn("could not write", out)
            self.assertFalse((repo / setup.CONFIG).exists() or (repo / setup.PART).exists())
            code, out = self.run_main(repo, Path(tmp) / "issue.md")
            self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
