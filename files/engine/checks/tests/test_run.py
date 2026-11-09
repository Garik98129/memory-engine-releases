import contextlib
import io
import unittest

from engine.checks import run
from datetime import datetime, timezone

from engine.checks.tests.helpers import Repo, body

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


class Runner(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def call(self, *args, base=None, text=None):
        out = io.StringIO()
        extra = [] if "--manifest-url" in args else ["--body", str(self.repo.body_file(text or body("user/fields/chat.md")))]
        with contextlib.redirect_stdout(out):
            code = run.main(["--repo", str(self.repo.path), "--base", base or self.repo.base,
                             "--kind", "person", *extra, *args], now=NOW)
        return code, out.getvalue()

    def test_clean_pr_exits_zero(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)))
        self.assertEqual(code, 0)
        self.assertIn("SUMMARY block=0 ran=C1,C2,C3,C4,C5,C6,C7", out)

    def test_blocking_pr_exits_one(self):
        head = self.repo.commit({"engine/rules/core.md": b"Weakened.\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)))
        self.assertEqual(code, 1)
        self.assertIn("C1 BLOCK engine/rules/core.md differs from release v0.1.0", out)
        self.assertIn("C2 BLOCK engine/rules/core.md is outside the person area", out)

    def test_a_check_that_cannot_run_is_loud(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.path / "no-such-manifest.json"))
        self.assertEqual(code, 2)
        self.assertIn("ERROR a check could not run", out)

    def test_a_pr_from_an_older_main_is_judged_by_what_lands(self):
        pr = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.repo.checkout(self.repo.base)
        main = self.repo.commit({"user/routines/morning.md": b"Morning.\n"})
        code, out = self.call("--head", pr, "--manifest", str(self.repo.manifest_file(main)), base=main)
        self.assertEqual(code, 0, out)   # the routine main added is not part of this PR

    def test_a_conflict_with_main_is_loud(self):
        pr = self.repo.commit({"user/fields/chat.md": b"Who: one.\n"})
        self.repo.checkout(self.repo.base)
        main = self.repo.commit({"user/fields/chat.md": b"Who: two.\n"})
        code, out = self.call("--head", pr, "--manifest", str(self.repo.manifest_file(main)), base=main)
        self.assertEqual(code, 2)
        self.assertIn("conflicts with main in user/fields/chat.md", out)

    def test_manifest_url_is_the_installed_tag_in_the_public_releases(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        code, out = self.call("--head", head, "--manifest-url")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "https://raw.githubusercontent.com/Garik98129/memory-engine-releases"
                                      "/refs/tags/v0.1.0/release-manifest.json")

    def test_manifest_url_for_an_update_names_the_new_tag(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v0.2.0"\n'})
        out = run.manifest_url(self.repo.path, self.repo.base, head, "update")
        self.assertTrue(out.endswith("/refs/tags/v0.2.0/release-manifest.json"), out)

    def test_manifest_url_refuses_a_releases_repository_that_is_not_owner_name(self):
        for bad in ("", "Garik98129", "a/b/c", "x/..", "x/.", "x/y?z", "https://evil.example/x"):
            defaults = b'[release]\nmanifests = "' + bad.encode() + b'"\n'
            head = self.repo.commit({"engine/defaults.toml": defaults})
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run.manifest_url(self.repo.path, head, head, "person")

    def test_the_old_template_key_alone_is_refused_not_followed(self):
        head = self.repo.commit({"engine/defaults.toml": b'[release]\ntemplate = "Garik98129/memory-engine"\n'})
        with self.assertRaisesRegex(ValueError, "release.manifests"):
            run.manifest_url(self.repo.path, head, head, "person")


    def test_a_bug_in_a_check_is_exit_two_not_a_traceback(self):
        from unittest import mock
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        with mock.patch("engine.checks.config_check.check", side_effect=TypeError("boom")):
            code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)))
        self.assertEqual(code, 2)
        self.assertIn("ERROR a check could not run: TypeError: boom", out)

    def test_machine_record_states_that_c7_does_not_apply(self):
        head = self.repo.commit({"user/fields/installed.toml": b'chat = "0123abcd"\n'})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)),
                              text="")
        self.assertEqual(code, 0, out)
        self.assertIn("C7 N/A machine record only", out)
        self.assertIn("ran=C1,C2,C3,C4,C5,C6\n", out + "\n")

    def test_a_record_pr_is_held_to_its_masters_at_once(self):
        # Issue #18, item 33: the first installation's record PR hashed a routine with its last
        # newline; N5 would have said so 36 h later. The gate says it on the PR (C2).
        from engine.checks import drift
        routine = b"Every morning: the calendar.\n"
        main = self.repo.commit({"user/routines/morning.md": routine})
        for sha, code_wanted in ((drift.digest(routine.decode()), 1),
                                 (drift.digest(drift.routine_text(routine)), 0)):
            with self.subTest(code_wanted=code_wanted):
                self.repo.checkout(main)
                head = self.repo.commit({"user/fields/installed.toml": f'[routines.morning]\nsha256 = "{sha}"\n'.encode()})
                code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(main)), base=main, text="")
                self.assertEqual(code, code_wanted, out)
                if code_wanted:
                    self.assertIn("C2 BLOCK user/fields/installed.toml records routine morning with a sha256 that is not "
                                  "of its master", out)

    def test_an_address_in_a_routine_master_blocks(self):
        # Issue #18, item 35: the routines hold no e-mail address (DOC-1).
        head = self.repo.commit({"user/routines/invitations.md": b"Accept from alex.trusted@example.com.\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)),
                              text=body("user/routines/invitations.md"))
        self.assertEqual(code, 1, out)
        self.assertIn("C4 BLOCK user/routines/invitations.md line 1: an e-mail address", out)
        self.assertNotIn("example.com", out)

    def test_body_without_the_record_blocks(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)),
                              text="Updated the field.")
        self.assertEqual(code, 1)
        self.assertIn("C7 BLOCK the PR body has no ## Change section", out)

    def test_manifest_needs_a_body(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run.main(["--repo", str(self.repo.path), "--base", self.repo.base, "--head", head,
                      "--kind", "person", "--manifest", "m.json"])


    def test_an_old_yes_blocks_through_the_runner(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        code, out = self.call("--head", head, "--manifest", str(self.repo.manifest_file(self.repo.base)),
                              text=body("user/fields/chat.md", approved="2020-01-01T00:00+00:00"))
        self.assertEqual(code, 1)
        self.assertIn("C7 BLOCK ## Approved: the yes is more than a day older than the change", out)


if __name__ == "__main__":
    unittest.main()
