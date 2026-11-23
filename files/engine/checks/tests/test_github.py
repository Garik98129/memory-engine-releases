import json
import unittest
from email.message import Message

from engine.checks.github import ApiError, GitHub


def headers(**values: str) -> Message:
    msg = Message()
    for k, v in values.items():
        msg[k.replace("_", "-")] = v
    return msg


class Recorded(GitHub):
    """The client with the network replaced by a script of answers."""

    def __init__(self, answers):
        super().__init__("owner/repo", "secret-token", api="https://api.example")
        self.answers = list(answers)
        self.sent = []

    def _send(self, method, url, body=None, auth=True):
        self.sent.append((method, url, auth))
        return self.answers.pop(0)


class RecordedOnce(GitHub):
    """The client with only the network call replaced: the retry stays in play."""

    def __init__(self, answers):
        super().__init__("owner/repo", "secret-token", api="https://api.example")
        self.retry_after = 0
        self.answers = list(answers)
        self.calls = 0

    def _once(self, method, url, body=None, auth=True):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class NoContent(unittest.TestCase):
    def test_a_dispatch_is_answered_204_or_200_and_anything_else_is_loud(self):
        for status in (204, 200):
            api = Recorded([(status, headers(), b"")])
            api.post_no_content("repos/owner/repo/actions/workflows/check.yml/dispatches", {"ref": "main"})
            self.assertEqual(api.sent, [("POST", "https://api.example/repos/owner/repo/actions/workflows/check.yml/dispatches", True)])
        for status in (201, 404, 422):
            with self.assertRaises(ApiError):
                Recorded([(status, headers(), b"")]).post_no_content("x", {})


class Reason(unittest.TestCase):
    """GitHub's own words come with the error as `reason`, not in its text: «HTTP 403» alone
    did not say why the update's PR was refused (on a test repository), and an
    alarm line that carried GitHub's wording would change whenever that wording does."""

    MESSAGE = "GitHub Actions is not permitted to create or approve pull requests."
    REFUSED = json.dumps({"message": MESSAGE, "documentation_url": "https://docs.github.com/rest"}).encode()

    def test_every_refusal_keeps_a_stable_text_and_githubs_reason_beside_it(self):
        calls = {
            "post": ("POST repos/owner/repo/pulls", lambda api: api.post("repos/owner/repo/pulls", {})),
            "post_no_content": ("POST repos/owner/repo/dispatches", lambda api: api.post_no_content("repos/owner/repo/dispatches", {})),
            "get": ("GET repos/owner/repo/pulls/1", lambda api: api.get("repos/owner/repo/pulls/1")),
            "pages": ("GET repos/owner/repo/pulls", lambda api: list(api.pages("repos/owner/repo/pulls"))),
            "text": ("GET repos/owner/repo/actions/jobs/1/logs", lambda api: api.text("repos/owner/repo/actions/jobs/1/logs")),
        }
        for name, (what, call) in calls.items():
            with self.subTest(name):
                with self.assertRaises(ApiError) as caught:
                    call(Recorded([(403, headers(), self.REFUSED)]))
                self.assertEqual(str(caught.exception), f"{what}: HTTP 403")
                self.assertEqual(caught.exception.reason, self.MESSAGE)

    def test_no_reason_where_github_gives_none_and_a_long_one_is_cut_to_one_line(self):
        for raw in (b"", b"<html>", b"[1, 2]", json.dumps({"message": "  "}).encode(), json.dumps({"message": 7}).encode()):
            with self.assertRaises(ApiError) as caught:
                Recorded([(403, headers(), raw)]).get("x")
            self.assertEqual((str(caught.exception), caught.exception.reason), ("GET x: HTTP 403", ""), raw)
        long = json.dumps({"message": "line one\nline two " + "x" * 400}).encode()
        with self.assertRaises(ApiError) as caught:
            Recorded([(422, headers(), long)]).post("x", {})
        reason = caught.exception.reason
        self.assertTrue(reason.startswith("line one line two xxx"), reason)
        self.assertEqual((len(reason), reason[-1]), (200, "…"))

    def test_a_network_error_names_the_address_without_its_query(self):
        api = GitHub("owner/repo", "secret-token", api="https://api.example")
        api._opener = type("Opener", (), {"open": lambda self, *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))})()
        with self.assertRaises(ApiError) as caught:
            api._once("GET", "https://logs.example/job/1?sig=SIGNED&se=2026")
        self.assertEqual(str(caught.exception), "GET https://logs.example/job/1: timed out")


class Retry(unittest.TestCase):
    def test_a_server_error_is_retried_once(self):
        api = RecordedOnce([(502, headers(), b""), (200, headers(), json.dumps({"ok": 1}).encode())])
        self.assertEqual(api.get("x"), {"ok": 1})
        self.assertEqual(api.calls, 2)

    def test_a_network_error_is_retried_once_then_loud(self):
        api = RecordedOnce([ApiError("reset"), ApiError("reset again")])
        with self.assertRaises(ApiError):
            api.get("x")
        self.assertEqual(api.calls, 2)

    def test_a_client_error_is_not_retried(self):
        api = RecordedOnce([(403, headers(), b"")])
        with self.assertRaises(ApiError):
            api.get("x")
        self.assertEqual(api.calls, 1)


class Client(unittest.TestCase):
    def test_pages_follow_the_link_header(self):
        api = Recorded([
            (200, headers(Link='<https://api.example/x?page=2>; rel="next"'), json.dumps([1, 2]).encode()),
            (200, headers(), json.dumps([3]).encode()),
        ])
        self.assertEqual(list(api.pages("x")), [1, 2, 3])
        self.assertEqual(api.sent[1][1], "https://api.example/x?page=2")

    def test_a_log_redirect_is_followed_without_the_token(self):
        api = Recorded([
            (302, headers(Location="https://logs.example/job.txt?sig=1"), b""),
            (200, headers(), b"line\n"),
        ])
        self.assertEqual(api.text("repos/owner/repo/actions/jobs/1/logs"), "line\n")
        self.assertEqual([s[2] for s in api.sent], [True, False])

    def test_a_redirect_off_https_is_refused(self):
        api = Recorded([(302, headers(Location="http://logs.example/job.txt"), b"")])
        with self.assertRaises(ApiError):
            api.text("logs")

    def test_an_error_is_loud_and_a_404_only_when_allowed(self):
        api = Recorded([(500, headers(), b""), (404, headers(), b"")])
        with self.assertRaises(ApiError):
            api.get("x")
        self.assertIsNone(api.get("y", allow_404=True))

    def test_a_broken_answer_is_an_api_error(self):
        api = Recorded([(200, headers(), b"<html>")])
        with self.assertRaises(ApiError):
            api.get("x")

    def test_a_network_error_is_an_api_error(self):
        api = GitHub("owner/repo", "t", api="https://api.example")
        api._opener = type("Opener", (), {"open": lambda self, *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))})()
        with self.assertRaises(ApiError):
            api.get("x")

    def test_no_token_no_client(self):
        with self.assertRaises(ApiError):
            GitHub("owner/repo", "")


if __name__ == "__main__":
    unittest.main()
