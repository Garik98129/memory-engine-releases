import unittest
from datetime import datetime, timedelta, timezone

from engine.checks import config_check, lock_check
from engine.checks.paths_check import PERSON, UPDATE
from engine.checks.tests.helpers import VALID_CONFIG, Repo

NOW = datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc)
FRESH = "2026-11-09T07:00:00Z"                  # published right now


def messages(findings):
    return [f.message for f in findings]


class EngineLock(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.manifest = self.repo.manifest(self.repo.base)

    def tearDown(self):
        self.repo.close()

    def test_person_pr_with_untouched_engine_passes(self):
        head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
        self.assertEqual(lock_check.check(self.repo.path, self.repo.base, head, PERSON, self.manifest), [])

    def test_changed_engine_file_is_red(self):
        head = self.repo.commit({"engine/rules/core.md": b"Weakened.\n"})
        self.assertIn("engine/rules/core.md differs from release v0.1.0",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, PERSON, self.manifest)))

    def test_extra_workflow_is_red(self):
        head = self.repo.commit({".github/workflows/sneaky.yml": b"name: check\n"})
        self.assertIn(".github/workflows/sneaky.yml is not part of release v0.1.0",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, PERSON, self.manifest)))

    def test_missing_engine_file_is_red(self):
        head = self.repo.commit({"engine/rules/core.md": None})
        self.assertIn("engine/rules/core.md is missing",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, PERSON, self.manifest)))

    def test_executable_engine_file_is_red(self):
        head = self.repo.chmod_exec("engine/rules/core.md")
        self.assertTrue(any("has mode 100755" in m for m in
                            messages(lock_check.check(self.repo.path, self.repo.base, head, PERSON, self.manifest))))

    def test_manifest_of_another_release_is_red(self):
        head = self.repo.commit({"user/fields/chat.md": b"x\n"})
        other = dict(self.manifest, release="v0.2.0")
        self.assertIn("manifest is v0.2.0, but the installed release is v0.1.0",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, PERSON, other)))

    def test_update_to_a_newer_release_passes(self):
        head = self.repo.commit({"engine/rules/core.md": b"New rules.\n",
                                 "user/engine.lock": b'release = "v0.2.0"\n'})
        manifest = self.repo.manifest(head, "v0.2.0")
        self.assertEqual(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW), [])

    def update(self, base: str | None = None, published: str | None = FRESH) -> tuple[str, dict]:
        """An update PR to v0.2.0 on top of base, and its manifest published at `published`."""
        self.repo.checkout(base or self.repo.base)
        head = self.repo.commit({"engine/rules/core.md": b"New rules.\n",
                                 "user/engine.lock": b'release = "v0.2.0"\n'})
        return head, self.repo.manifest(head, "v0.2.0", published)

    def on_main(self, config: bytes) -> str:
        """main with this config: the base an update PR is checked against."""
        self.repo.checkout(self.repo.base)
        return self.repo.commit({"user/config.toml": config})

    def test_a_newer_release_waits_a_week_on_the_stable_channel(self):
        head, manifest = self.update()
        for late in (timedelta(0), timedelta(days=7) - timedelta(seconds=1)):
            self.assertEqual(messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW + late)),
                             ["v0.2.0 is due on the stable channel at 2026-11-16T07:00:00Z, not yet"])
        self.assertEqual(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest,
                                          NOW + timedelta(days=7)), [])

    def test_canary_takes_a_release_at_once(self):
        base = self.on_main(VALID_CONFIG.replace(b'channel = "stable"', b'channel = "canary"'))
        head, manifest = self.update(base)
        self.assertEqual(lock_check.check(self.repo.path, base, head, UPDATE, manifest, NOW), [])
        self.assertIn("v0.2.0 is due on the canary channel at 2026-11-09T07:00:00Z, not yet",
                      messages(lock_check.check(self.repo.path, base, head, UPDATE, manifest,
                                                NOW - timedelta(seconds=1))))   # not before it is published

    def test_the_channel_is_mains_not_the_prs(self):
        self.repo.checkout(self.repo.base)
        head = self.repo.commit({"engine/rules/core.md": b"New rules.\n",
                                 "user/engine.lock": b'release = "v0.2.0"\n',
                                 "user/config.toml": VALID_CONFIG.replace(b'channel = "stable"', b'channel = "canary"')})
        manifest = self.repo.manifest(head, "v0.2.0", FRESH)
        self.assertIn("v0.2.0 is due on the stable channel at 2026-11-16T07:00:00Z, not yet",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW)))

    def test_a_channel_main_does_not_name_validly_waits_the_longest(self):
        for config in (VALID_CONFIG.replace(b'channel = "stable"', b'channel = "nightly"'),
                       VALID_CONFIG.replace(b'[engine]\nchannel = "stable"\n', b""),
                       b'engine = "canary"\n' + VALID_CONFIG.replace(b'[engine]\nchannel = "stable"\n', b""),
                       VALID_CONFIG.replace(b'channel = "stable"', b'channel = ["canary"]'),
                       VALID_CONFIG.replace(b'channel = "stable"', b'channel = { name = "canary" }'),
                       b"not toml ["):
            base = self.on_main(config)
            head, manifest = self.update(base)
            self.assertIn("v0.2.0 is due on the stable channel at 2026-11-16T07:00:00Z, not yet",
                          messages(lock_check.check(self.repo.path, base, head, UPDATE, manifest, NOW)), config)

    def test_an_update_to_a_release_without_a_valid_publish_time_is_red(self):
        for published in (None, "2026-11-09", "2026-11-09T07:00:00+00:00", "soon"):
            head, manifest = self.update(published=published)
            self.assertIn("the manifest of v0.2.0 has no valid «published» time",
                          messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest,
                                                    NOW + timedelta(days=30))), published)
        head, manifest = self.update()
        manifest["published"] = 1762671600                                  # a number, not the factory's text
        self.assertIn("the manifest of v0.2.0 has no valid «published» time",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW)))

    def test_a_repair_and_a_save_do_not_wait_for_the_channel(self):
        broken = self.repo.commit({"engine/rules/core.md": b"Tampered.\n"})
        repaired = self.repo.commit({"engine/rules/core.md": b"Core rules.\n"})
        for published in (FRESH, None):                 # installed by hand the day it came out
            manifest = self.repo.manifest(self.repo.base, published=published)
            self.assertEqual(lock_check.check(self.repo.path, broken, repaired, UPDATE, manifest, NOW), [])
            head = self.repo.commit({"user/fields/chat.md": b"Who: changed.\n"})
            self.assertEqual(lock_check.check(self.repo.path, self.repo.base, head, PERSON, manifest, NOW), [])

    def test_a_rollback_is_named_as_a_rollback_not_as_not_due(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v0.0.9"\n'})
        manifest = self.repo.manifest(head, "v0.0.9", FRESH)
        self.assertEqual(messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW)),
                         ["update to v0.0.9 is older than the installed v0.1.0"])

    def test_every_channel_c3_allows_has_a_delay_and_the_fallback_is_the_slowest(self):
        self.assertEqual(set(lock_check.CHANNEL_DELAY), set(config_check.CHANNELS))
        self.assertEqual(lock_check.CHANNEL_DELAY[lock_check.SLOWEST], max(lock_check.CHANNEL_DELAY.values()))

    def test_rollback_is_refused(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v0.0.9"\n'})
        manifest = self.repo.manifest(head, "v0.0.9")
        self.assertIn("update to v0.0.9 is older than the installed v0.1.0",
                      messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest)))

    def test_repair_to_the_installed_release_passes(self):
        broken = self.repo.commit({"engine/rules/core.md": b"Tampered.\n"})
        repaired = self.repo.commit({"engine/rules/core.md": b"Core rules.\n"})
        self.assertEqual(lock_check.check(self.repo.path, broken, repaired, UPDATE, self.manifest), [])

    def test_wanted_release_person_reads_main_update_reads_the_pr(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v0.2.0"\n'})
        self.assertEqual(lock_check.wanted_release(self.repo.path, self.repo.base, head, PERSON), "v0.1.0")
        self.assertEqual(lock_check.wanted_release(self.repo.path, self.repo.base, head, UPDATE), "v0.2.0")

    def test_wanted_release_refuses_a_tag_unfit_for_a_url(self):
        head = self.repo.commit({"user/engine.lock": b'release = "v1/../../x"\n'})
        with self.assertRaises(ValueError):
            lock_check.wanted_release(self.repo.path, self.repo.base, head, UPDATE)

    def test_update_lock_must_name_the_manifest_release(self):
        head = self.repo.commit({"engine/rules/core.md": b"New rules.\n"})
        manifest = self.repo.manifest(head, "v0.2.0")
        self.assertEqual(messages(lock_check.check(self.repo.path, self.repo.base, head, UPDATE, manifest, NOW)),
                         ["user/engine.lock names v0.1.0, the manifest is v0.2.0"])

    def test_bad_release_tag_is_an_error(self):
        for tag in ("0.1", 1, None):
            with self.assertRaises(ValueError):
                lock_check.release_key(tag)


if __name__ == "__main__":
    unittest.main()
