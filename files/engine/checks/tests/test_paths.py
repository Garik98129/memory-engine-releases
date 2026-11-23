import unittest

from engine.checks import paths_check
from engine.checks.paths_check import PERSON, UPDATE
from engine.checks.tests.helpers import Repo


def messages(findings):
    return [f.message for f in findings]


class PersonArea(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def run_check(self, head, kind=PERSON):
        return paths_check.check(self.repo.path, self.repo.base, head, kind)

    def test_field_master_change_passes(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(self.run_check(head), [])

    def test_new_routine_passes(self):
        head = self.repo.commit({"user/routines/morning-calendar.md": b"Routine.\n"})
        self.assertEqual(self.run_check(head), [])

    def test_the_installed_record_goes_in_a_pr_of_its_own(self):
        alone = self.repo.commit({"user/fields/installed.toml": b'[fields.chat]\nsha256 = "0"\n'})
        self.assertEqual(self.run_check(alone), [])
        self.repo.checkout(self.repo.base)
        mixed = self.repo.commit({"user/fields/installed.toml": b'[fields.chat]\nsha256 = "0"\n',
                                  "user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(messages(self.run_check(mixed)),
                         ["user/fields/installed.toml is the record of an installation: a PR of its own, "
                          "after the master it records has landed"])

    def test_routine_with_bad_name_is_outside(self):
        head = self.repo.commit({"user/routines/Morning Calendar.md": b"Routine.\n"})
        self.assertIn("outside the person area", messages(self.run_check(head))[0])

    def test_engine_change_is_outside(self):
        head = self.repo.commit({"engine/rules/core.md": b"Weakened.\n"})
        self.assertIn("engine/rules/core.md is outside the person area", messages(self.run_check(head)))

    def test_lock_is_outside_for_a_person(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v9.9.9"\n'})
        self.assertIn("user/engine.lock is outside the person area", messages(self.run_check(head)))

    def test_rename_into_engine_fails_on_the_add_half(self):
        self.repo.write("user/fields/chat.md", None)
        head = self.repo.commit({"engine/rules/extra.md": b"Who: a test person.\n"})
        msgs = messages(self.run_check(head))
        self.assertIn("engine/rules/extra.md is outside the person area", msgs)

    def test_symlink_is_red(self):
        head = self.repo.symlink("user/fields/bot.md", "/etc/passwd")
        self.assertIn("user/fields/bot.md is a symlink", messages(self.run_check(head)))

    def test_executable_bit_is_red(self):
        head = self.repo.chmod_exec("user/fields/chat.md")
        self.assertIn("user/fields/chat.md is an executable bit", messages(self.run_check(head)))

    def test_non_utf8_name_is_red(self):
        head = self.repo.commit({b"user/routines/\xff.md": b"x\n"})
        self.assertTrue(any("not valid UTF-8" in m for m in messages(self.run_check(head))))

    def test_uppercase_twin_of_a_person_file_is_outside(self):
        head = self.repo.commit({"user/fields/Chat.md": b"Who: twin.\n"})
        self.assertIn("user/fields/Chat.md is outside the person area", messages(self.run_check(head)))

    def test_empty_pr_is_red(self):
        head = self.repo.commit({})
        self.assertEqual(messages(self.run_check(head)), ["the PR changes nothing"])


class UpdateArea(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def test_engine_workflow_and_lock_pass(self):
        head = self.repo.commit({
            "engine/rules/core.md": b"New core rules.\n",
            ".github/workflows/check.yml": b"name: check v2\n",
            "user/engine.lock": b'release = "v0.2.0"\n',
        })
        self.assertEqual(paths_check.check(self.repo.path, self.repo.base, head, UPDATE), [])

    def test_case_collision_is_red(self):
        head = self.repo.commit({"engine/rules/Core.md": b"Twin rules.\n",
                                 "user/engine.lock": b'release = "v0.2.0"\n'})
        msgs = messages(paths_check.check(self.repo.path, self.repo.base, head, UPDATE))
        self.assertIn("engine/rules/Core.md collides by letter case with engine/rules/core.md", msgs)

    def test_update_may_not_touch_the_person_part(self):
        head = self.repo.commit({"user/config.toml": b"[person]\n"})
        msgs = messages(paths_check.check(self.repo.path, self.repo.base, head, UPDATE))
        self.assertIn("user/config.toml is outside the update area", msgs)

    def test_unknown_kind_is_an_error(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        with self.assertRaises(ValueError):
            paths_check.check(self.repo.path, self.repo.base, head, "other")


if __name__ == "__main__":
    unittest.main()
