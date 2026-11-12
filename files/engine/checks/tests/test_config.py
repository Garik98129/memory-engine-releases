import unittest

from engine.checks import config_check
from engine.checks.paths_check import PERSON
from engine.checks.tests.helpers import VALID_CONFIG, Repo


def messages(findings):
    return [f.message for f in findings]


class Config(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def run_with(self, config: bytes, extra=None):
        head = self.repo.commit({"user/config.toml": config, **(extra or {})})
        return messages(config_check.check(self.repo.path, self.repo.base, head, PERSON))

    def test_valid_config_passes(self):
        self.assertEqual(self.run_with(VALID_CONFIG + b"\n[notion]\nshrink_alarm = 0.1\n"), [])

    def test_the_bot_cannot_set_or_change_protected_keys(self):
        msgs = self.run_with(VALID_CONFIG + b"\n[alarm]\ntrusted_contact = \"someone\"\n")
        self.assertEqual(msgs, ["alarm.trusted_contact changes only in a push of the trusted contact's own"])

    def test_only_the_trusted_contacts_own_push_changes_protected_keys(self):
        with_contact = VALID_CONFIG + b"\n[alarm]\ntrusted_contact = \"Contact\"\n"
        main = self.repo.commit({"user/config.toml": with_contact})
        canary = with_contact.replace(b'channel = "stable"', b'channel = "canary"')
        changed = self.repo.commit({"user/config.toml": canary})
        later = self.repo.commit({"user/fields/chat.md": b"Who: later.\n"})
        red = ["engine.channel changes only in a push of the trusted contact's own"]
        check = lambda head, actor, before: messages(config_check.check(self.repo.path, main, head, PERSON, actor, before))
        # the trusted contact's own push brings the change: the head before it still matched main
        self.assertEqual(check(changed, "contact", main), [])
        # the bot's push
        self.assertEqual(check(changed, "alex-bot", main), red)
        # the bot slipped the change in earlier; the trusted contact pushes on top — still red
        self.assertEqual(check(later, "contact", changed), red)
        # an open, a reopen, an edit or a dispatch: the gate knows neither pusher nor before
        self.assertEqual(check(changed, "contact", ""), red)
        self.assertEqual(check(changed, "", ""), red)
        # a force push: `before` is not in the repository — nothing is proven, no crash
        self.assertEqual(check(changed, "contact", "0" * 40), red)
        silenced = with_contact.replace(b'trusted_contact = "Contact"', b'trusted_contact = ""')
        head = self.repo.commit({"user/config.toml": silenced})
        self.assertEqual(check(head, "alex-bot", main),
                         ["alarm.trusted_contact changes only in a push of the trusted contact's own"])

    def at_main(self, config: bytes):
        head = self.repo.commit({"user/config.toml": config})
        return messages(config_check.check(self.repo.path, head, head, PERSON))

    def test_the_persons_login_and_the_marks_issue(self):
        with_login = lambda login: VALID_CONFIG.replace(b'timezone = "Asia/Jerusalem"\n',
                                                        b'timezone = "Asia/Jerusalem"\ngithub_login = "' + login + b'"\n')
        self.assertEqual(self.at_main(with_login(b"alex")), [])
        self.assertEqual(self.at_main(with_login(b"not a login!")), ["person.github_login 'not a login!' is not a GitHub login"])
        msgs = self.run_with(VALID_CONFIG + b"\n[alarm]\nmarks_issue = 7\n")
        self.assertEqual(msgs, ["alarm.marks_issue changes only in a push of the trusted contact's own"])
        msgs = self.run_with(VALID_CONFIG + b"\n[alarm]\nmarks_issue = 0\n")
        self.assertIn("alarm.marks_issue must be a positive Issue number", msgs)

    def test_the_bot_cannot_point_the_marks_watch_at_another_account(self):
        on_main = VALID_CONFIG.replace(b'timezone = "Asia/Jerusalem"\n', b'timezone = "Asia/Jerusalem"\ngithub_login = "alex"\n') \
            + b'\n[alarm]\ntrusted_contact = "Contact"\n'
        main = self.repo.commit({"user/config.toml": on_main})
        red = ["person.github_login changes only in a push of the trusted contact's own"]
        check = lambda head, actor, before: messages(config_check.check(self.repo.path, main, head, PERSON, actor, before))
        for other in (b'github_login = "someone-else"', b'github_login = ""'):
            head = self.repo.commit({"user/config.toml": on_main.replace(b'github_login = "alex"', other)})
            self.assertEqual(check(head, "alex", main), red, other)
            self.assertEqual(check(head, "contact", main), [], other)    # a new bot account: the trusted contact's push
            self.repo.checkout(main)
        unset = self.repo.commit({"user/config.toml": on_main.replace(b'github_login = "alex"\n', b"")})
        self.assertEqual(check(unset, "alex", main), red)                # unset counts any account's marks

    def test_c4_patterns_may_be_added_never_dropped_by_the_bot(self):
        on_main = VALID_CONFIG + b'\n[alarm]\ntrusted_contact = "Contact"\n'
        main = self.repo.commit({"user/config.toml": on_main})
        check = lambda cfg, actor="alex", before=main: messages(config_check.check(
            self.repo.path, main, self.repo.commit({"user/config.toml": cfg}), PERSON, actor, before))
        dropped = "C4 would stop looking for il_id, il_phone (person.country or personal.patterns): " \
                  "only in a push of the trusted contact's own"
        moved = on_main.replace(b'country = "IL"', b'country = "NZ"')
        self.assertEqual(check(moved), [dropped])                        # the first of two PRs: country, then the number
        self.assertEqual(check(moved, "contact"), [])                   # she moved: the trusted contact writes it
        self.assertEqual(check(moved, "contact", ""), [dropped])         # a dispatch or an open trusts nobody
        kept = moved + b'\n[personal]\npatterns = ["il_id", "il_phone"]\n'
        self.assertEqual(check(kept), [])                               # the same patterns by another road
        self.repo.checkout(main)
        slipped = self.repo.commit({"user/config.toml": moved})         # the bot's push, then the trusted contact's on top
        on_top = self.repo.commit({"user/fields/chat.md": b"Who: later.\n"})
        self.assertEqual(messages(config_check.check(self.repo.path, main, on_top, PERSON, "contact", slipped)), [dropped])
        self.repo.checkout(main)
        own = on_main + b'\n[personal]\npatterns = ["iban"]\n'
        self.assertEqual(check(own), [])                                # already on everywhere: no change
        self.repo.checkout(main)
        main_card = self.repo.commit({"user/config.toml": on_main + b'\n[personal]\npatterns = ["card"]\n'})
        msgs = messages(config_check.check(self.repo.path, main_card, self.repo.commit({"user/config.toml": on_main}),
                                           PERSON, "alex", main_card))
        self.assertEqual(msgs, [])                                      # the payment floor applies anyway
        main2 = self.repo.commit({"user/config.toml": VALID_CONFIG.replace(b'country = "IL"', b'country = "NZ"')
                                  + b'\n[personal]\npatterns = ["il_id"]\n'})
        msgs = messages(config_check.check(self.repo.path, main2, self.repo.commit(
            {"user/config.toml": VALID_CONFIG.replace(b'country = "IL"', b'country = "NZ"')}), PERSON, "alex", main2))
        self.assertEqual(msgs, ["C4 would stop looking for il_id (person.country or personal.patterns): "
                                "only in a push of the trusted contact's own"])
        self.repo.checkout(main2)
        back = VALID_CONFIG + b'\n[personal]\npatterns = ["il_id"]\n'    # NZ → IL: tightens, no one is asked
        self.assertEqual(messages(config_check.check(self.repo.path, main2, self.repo.commit(
            {"user/config.toml": back}), PERSON, "alex", main2)), [])

    def test_the_bot_cannot_take_a_page_out_of_the_notion_copy(self):
        on_main = VALID_CONFIG + b'\n[alarm]\ntrusted_contact = "Contact"\n\n[notion]\nbackup_exclude = ["aaaa-1111"]\n'
        main = self.repo.commit({"user/config.toml": on_main})
        check = lambda cfg, actor="alex": messages(config_check.check(
            self.repo.path, main, self.repo.commit({"user/config.toml": cfg}), PERSON, actor, main))
        red = ["notion.backup_exclude leaves a new page out of the copy: only in a push of the trusted contact's own"]
        more = on_main.replace(b'["aaaa-1111"]', b'["aaaa-1111", "docs"]')
        self.assertEqual(check(more), red)
        self.assertEqual(check(more, "contact"), [])
        self.assertEqual(check(on_main.replace(b'["aaaa-1111"]', b'["AAAA1111"]')), [])   # the same id, spelled otherwise
        self.assertEqual(check(on_main.replace(b'["aaaa-1111"]', b"[]")), [])            # copying more tightens
        self.repo.checkout(main)
        slipped = self.repo.commit({"user/config.toml": more})
        on_top = self.repo.commit({"user/fields/chat.md": b"Who: later.\n"})
        self.assertEqual(messages(config_check.check(self.repo.path, main, on_top, PERSON, "contact", slipped)), red)

    def test_turning_off_the_protection_alarm_is_a_protected_change(self):
        msgs = self.run_with(VALID_CONFIG + b'\n[github]\nprotection = "none"\n')
        self.assertEqual(msgs, ["github.protection changes only in a push of the trusted contact's own"])

    def test_writing_out_the_default_protection_changes_nothing(self):
        self.assertEqual(self.run_with(VALID_CONFIG + b'\n[github]\nprotection = "required"\n'), [])

    def test_an_unknown_protection_mode_is_red(self):
        on_main = VALID_CONFIG + b'\n[github]\nprotection = "maybe"\n'
        head = self.repo.commit({"user/config.toml": on_main})
        msgs = messages(config_check.check(self.repo.path, head, head, PERSON))
        self.assertEqual(msgs, ["github.protection must be one of none, required"])

    def test_the_trusted_contact_is_a_github_login(self):
        for bad in ("-lead", "two--hyphens", "a" * 40, "with space", "@helper"):
            on_main = VALID_CONFIG + f'\n[alarm]\ntrusted_contact = "{bad}"\n'.encode()
            head = self.repo.commit({"user/config.toml": on_main})
            msgs = messages(config_check.check(self.repo.path, head, head, PERSON))
            self.assertEqual(msgs, [f"alarm.trusted_contact {bad!r} is not a GitHub login"], bad)

    def test_non_text_patterns_are_reported_not_crashed_on(self):
        msgs = self.run_with(VALID_CONFIG + b"\n[personal]\npatterns = [{a = 1}, \"card\"]\n")
        self.assertEqual(msgs, ["unknown personal patterns: {'a': 1}"])

    def test_tightening_passes(self):
        self.assertEqual(self.run_with(VALID_CONFIG.replace(b"chat_field_limit = 4000", b"chat_field_limit = 3500")), [])

    def test_loosening_a_floor_is_red(self):
        msgs = self.run_with(VALID_CONFIG.replace(b"chat_field_limit = 4000", b"chat_field_limit = 5000"))
        self.assertIn("platform.chat_field_limit = 5000 loosens the floor 4000", msgs)

    def test_floors_come_from_base_not_from_the_pr(self):
        loose_defaults = b"[floors]\nchat_field_limit_max = 9000\nbot_field_limit_max = 7900\n" \
                         b"routine_limit_max = 4000\nbackup_shrink_alarm_max = 0.2\n"
        msgs = self.run_with(VALID_CONFIG.replace(b"chat_field_limit = 4000", b"chat_field_limit = 5000"),
                             {"engine/defaults.toml": loose_defaults})
        self.assertIn("platform.chat_field_limit = 5000 loosens the floor 4000", msgs)

    def test_unknown_key_is_red(self):
        # appended after the last table, so it lands in [engine]
        self.assertIn("unknown key engine.speed", self.run_with(VALID_CONFIG + b"speed = 3\n"))

    def test_unknown_section_is_red(self):
        self.assertIn("unknown section [secrets]", self.run_with(VALID_CONFIG + b"\n[secrets]\nx = 1\n"))

    def test_missing_required_key_is_red(self):
        msgs = self.run_with(VALID_CONFIG.replace(b'timezone = "Asia/Jerusalem"\n', b""))
        self.assertIn("required key person.timezone is missing", msgs)

    def test_wrong_type_is_red(self):
        msgs = self.run_with(VALID_CONFIG.replace(b"bot_field_limit = 7900", b'bot_field_limit = "7900"'))
        self.assertIn("platform.bot_field_limit must be int", msgs)

    def test_the_exclusions_hold_strings_only(self):
        # The copy keeps only strings, so [123] read as «exclude
        # nothing». The other lists name each wrong item themselves (the tests around).
        for items in (b"[123]", b'["aaaa-1111", 2]'):
            with self.subTest(items=items):
                self.assertIn("notion.backup_exclude must be a list of strings",
                              self.run_with(VALID_CONFIG + b"\n[notion]\nbackup_exclude = " + items + b"\n"))

    def test_bool_is_not_an_int(self):
        msgs = self.run_with(VALID_CONFIG.replace(b"bot_field_limit = 7900", b"bot_field_limit = true"))
        self.assertIn("platform.bot_field_limit must be int", msgs)

    def test_unknown_pattern_is_red(self):
        msgs = self.run_with(VALID_CONFIG + b'\n[personal]\npatterns = ["passport_xx"]\n')
        self.assertIn("unknown personal patterns: passport_xx", msgs)

    def test_languages_and_country_pass_in_any_language(self):
        cfg = VALID_CONFIG.replace(b'language = "ru"', b'language = "pt-BR"\nwork_languages = ["he", "en"]')
        self.assertEqual(self.at_main(cfg.replace(b'country = "IL"', b'country = "BR"')), [])

    def test_bad_language_or_country_is_red(self):
        cfg = VALID_CONFIG.replace(b'language = "ru"', b'language = "Russian"\nwork_languages = ["he", 5]')
        msgs = self.at_main(cfg.replace(b'country = "IL"', b'country = "Israel"'))
        self.assertEqual(sorted(msgs), sorted([
            "language 'Russian' is not a language code like ru or pt-BR",
            "language 5 is not a language code like ru or pt-BR",
            "person.country 'Israel' is not a two-letter code like IL"]))

    def test_country_is_required(self):
        self.assertIn("required key person.country is missing",
                      self.run_with(VALID_CONFIG.replace(b'country = "IL"\n', b"")))

    def test_a_removed_base_is_recorded_as_its_id_and_day(self):
        good = self.run_with(VALID_CONFIG + b'\n[notion]\nremoved_bases = ["docs@2026-11-09", "1111-2222@2026-11-10"]\n')
        self.assertFalse([m for m in good if "removed_bases" in m], good)
        for entry in ("docs", "docs@9.11.2026", "@2026-11-09", "docs@2026-11-09 "):
            msgs = self.run_with(VALID_CONFIG + f'\n[notion]\nremoved_bases = ["{entry}"]\n'.encode())
            self.assertIn(f"notion.removed_bases entry {entry!r} is not <id>@<YYYY-MM-DD>, the base and the day "
                          f"as N3's line names them", msgs, entry)

    ROUTINES = {"engine/rules/routines.md": b"## Task 3 \xe2\x80\x94 mark\n\nRoutine `mark`, task version 2; never declined.\n\n"
                                            b"## Task 5 \xe2\x80\x94 walk\n\nRoutine `walk-2`, task version 1.\n"}

    def test_a_declined_task_version_is_recorded_as_its_routine_and_version(self):
        good = self.run_with(VALID_CONFIG + b'declined = ["walk-2@1"]\n', self.ROUTINES)
        self.assertFalse([m for m in good if "declined" in m], good)
        for entry in ("mark", "mark@v2", "Mark@2", "mark@0", "mark@2 "):
            msgs = self.run_with(VALID_CONFIG + f'declined = ["{entry}"]\n'.encode())
            self.assertIn(f"engine.declined entry {entry!r} is not <routine>@<task version>, the routine and the "
                          f"version N7's line names", msgs, entry)
        self.assertIn("engine.declined must be a list of strings",
                      self.run_with(VALID_CONFIG + b'declined = [2]\n'))

    def test_only_a_version_on_her_list_now_can_be_declined_and_never_task_3(self):
        for entry in ("walk-2@2", "nosuch@1"):                      # a future version, a routine that is no task
            self.assertEqual(self.run_with(VALID_CONFIG + f'declined = ["{entry}"]\n'.encode(), self.ROUTINES),
                             [f"engine.declined entry {entry!r} is not a task's current version in "
                              f"engine/rules/routines.md: only a version on her list now can be declined"])
        self.assertEqual(self.run_with(VALID_CONFIG + b'declined = ["mark@2"]\n', self.ROUTINES),
                         ["engine.declined entry 'mark@2': task 3 says \u00abnever declined\u00bb"])
        # One she has is not judged again: the task moved on, the entry stops counting, nothing is red.
        main = self.repo.commit({"user/config.toml": VALID_CONFIG + b'declined = ["walk-2@1"]\n', **self.ROUTINES})
        head = self.repo.commit({"engine/rules/routines.md": self.ROUTINES["engine/rules/routines.md"].replace(
            b"`walk-2`, task version 1", b"`walk-2`, task version 2")})
        self.assertEqual(messages(config_check.check(self.repo.path, main, head, PERSON)), [])

    def test_bad_channel_is_red(self):
        msgs = self.run_with(VALID_CONFIG.replace(b'channel = "stable"', b'channel = "beta"'))
        self.assertIn("engine.channel must be one of canary, stable", msgs)

    def test_bad_timezone_is_red(self):
        msgs = self.run_with(VALID_CONFIG.replace(b"Asia/Jerusalem", b"Mars/Olympus"))
        self.assertIn("unknown timezone 'Mars/Olympus'", msgs)

    def test_shrink_alarm_above_floor_is_red(self):
        msgs = self.run_with(VALID_CONFIG + b"\n[notion]\nshrink_alarm = 0.5\n")
        self.assertIn("notion.shrink_alarm = 0.5 loosens the floor 0.2", msgs)

    def test_parse_error_is_red(self):
        msgs = self.run_with(b"[person\n")
        self.assertTrue(msgs and msgs[0].startswith("user/config.toml does not parse"))

    def test_missing_config_is_red(self):
        head = self.repo.commit({"user/config.toml": None})
        self.assertIn("user/config.toml is missing",
                      messages(config_check.check(self.repo.path, self.repo.base, head, PERSON)))


class ReadForCopy(unittest.TestCase):
    """The
    copy never reads a config it cannot read whole as «exclude nothing»."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.close()

    def read(self, config):
        return config_check.read_for_copy(self.repo.path, self.repo.commit({"user/config.toml": config}))

    def refused(self, config) -> str:
        with self.assertRaises(ValueError) as cm:
            self.read(config)
        self.assertTrue(str(cm.exception).startswith("the settings are not usable: "), cm.exception)
        return str(cm.exception)

    def test_a_good_config_reads_as_read_does(self):
        for cfg in (VALID_CONFIG + b'\n[notion]\nbackup_exclude = ["aaaa-1111", "BBBB2222"]\nshrink_alarm = 0.1\n',
                    VALID_CONFIG):                                                # no [notion]: the default, open
            with self.subTest(cfg=cfg[-40:]):
                head = self.repo.commit({"user/config.toml": cfg})
                self.assertEqual(config_check.read_for_copy(self.repo.path, head), config_check.read(self.repo.path, head))

    def test_a_missing_or_broken_file_is_refused(self):
        self.assertIn("user/config.toml does not parse", self.refused(b"[notion\n"))
        head = self.repo.commit({"user/config.toml": None})
        with self.assertRaises(ValueError) as cm:
            config_check.read_for_copy(self.repo.path, head)
        self.assertIn("user/config.toml is missing", str(cm.exception))
        self.assertEqual(config_check.read(self.repo.path, head), {})                # read stays lenient for the alarms

    def test_a_mistyped_key_section_or_type_is_refused(self):
        self.assertIn("unknown key notion.backup_exlude", self.refused(VALID_CONFIG + b'\n[notion]\nbackup_exlude = ["aaaa"]\n'))
        self.assertIn("unknown section [notoin]", self.refused(VALID_CONFIG + b'\n[notoin]\nbackup_exclude = ["aaaa"]\n'))
        self.assertIn("notion.backup_exclude must be a list of strings", self.refused(VALID_CONFIG + b'\n[notion]\nbackup_exclude = "aaaa"\n'))
        for items in (b"[123]", b'["aaaa-1111", 2]', b'[["aaaa"]]'):
            with self.subTest(items=items):
                self.assertIn("notion.backup_exclude must be a list of strings",
                              self.refused(VALID_CONFIG + b"\n[notion]\nbackup_exclude = " + items + b"\n"))
        self.assertIn("[notion] is not a table", self.refused(b'notion = 1\n' + VALID_CONFIG))   # before any table header

    def test_other_sections_values_are_c3s_not_the_copys(self):
        cfg = VALID_CONFIG.replace(b'channel = "stable"', b'channel = "nightly"')        # C3's finding, not the copy's
        self.assertEqual(self.read(cfg)["engine"]["channel"], "nightly")
        self.assertIn("unknown section [persn]", self.refused(VALID_CONFIG + b'\n[persn]\nx = 1\n'))   # any unknown section stops it


if __name__ == "__main__":
    unittest.main()
