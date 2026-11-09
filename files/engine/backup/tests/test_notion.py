import json
import unittest
from email.message import Message

from engine.backup.notion import ATTEMPTS, MAX_PAGES, Notion, NotionError


def headers(**values: str) -> Message:
    msg = Message()
    for k, v in values.items():
        msg[k.replace("_", "-")] = v
    return msg


def page(items, cursor=None, **extra):
    return 200, headers(), json.dumps({"object": "list", "results": items, "has_more": cursor is not None,
                                       "next_cursor": cursor, **extra}).encode()


class Scripted(Notion):
    """The client with the network replaced by a script of answers; time is a counter."""

    def __init__(self, answers):
        self.slept = []
        self.now = 0.0
        super().__init__("secret-token", api="https://api.example/v1",
                         sleep=self._sleep_fake, clock=lambda: self.now)
        self.answers = list(answers)
        self.sent = []

    def _sleep_fake(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds

    def _once(self, method, path, body=None):
        self.sent.append((method, path, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Lists(unittest.TestCase):
    def test_a_search_follows_the_cursor_and_sends_the_filter(self):
        api = Scripted([page([{"id": "a"}], "c1"), page([{"id": "b"}])])
        self.assertEqual([x["id"] for x in api.search("page")], ["a", "b"])
        self.assertEqual(api.sent[0], ("POST", "search", {"filter": {"property": "object", "value": "page"}, "page_size": 100}))
        self.assertEqual(api.sent[1][2]["start_cursor"], "c1")

    def test_children_page_through_the_query_string(self):
        api = Scripted([page([{"id": "x"}], "k"), page([])])
        self.assertEqual([b["id"] for b in api.children("blk")], ["x"])
        self.assertEqual(api.sent[0][1], "blocks/blk/children?page_size=100")
        self.assertEqual(api.sent[1][1], "blocks/blk/children?page_size=100&start_cursor=k")

    def test_a_list_notion_cut_short_is_loud(self):
        api = Scripted([page([{"id": "x"}], request_status={"type": "incomplete", "incomplete_reason": "query_result_limit_reached"})])
        with self.assertRaises(NotionError) as cm:
            list(api.rows("ds"))
        self.assertIn("cut the list short", str(cm.exception))

    def test_too_many_pages_is_loud(self):
        api = Scripted([page([{"id": "x"}], "again")] * (MAX_PAGES + 1))
        with self.assertRaises(NotionError):
            list(api.rows("ds"))

    def test_more_promised_without_a_cursor_is_loud_not_short(self):
        api = Scripted([(200, headers(), json.dumps({"object": "list", "results": [{"id": "a"}], "has_more": True,
                                                     "next_cursor": None}).encode())])
        with self.assertRaises(NotionError) as cm:
            list(api.search("page"))
        self.assertIn("has_more without next_cursor", str(cm.exception))

    def test_no_results_list_is_loud(self):
        api = Scripted([(200, headers(), b'{"object": "list"}')])
        with self.assertRaises(NotionError):
            list(api.search("page"))


class Transport(unittest.TestCase):
    def test_requests_keep_under_three_a_second(self):
        api = Scripted([page([]), page([]), page([])])
        for _ in range(3):
            list(api.search("page"))
        self.assertEqual(api.slept, [0.35, 0.35])

    def test_429_waits_retry_after_then_succeeds(self):
        api = Scripted([(429, headers(Retry_After="7"), b'{"code": "rate_limited"}'), page([{"id": "a"}])])
        self.assertEqual([x["id"] for x in api.search("page")], ["a"])
        self.assertEqual(api.slept, [7.0])
        self.assertEqual(api.requests, 2)

    def test_a_server_error_is_retried_with_a_pause(self):
        api = Scripted([(503, headers(), b""), (502, headers(), b""), page([])])
        list(api.search("page"))
        self.assertEqual(api.requests, 3)
        self.assertEqual(api.slept, [2.0, 4.0])             # a pause long enough needs no pacing after it

    def test_repeated_failures_give_up_loudly(self):
        api = Scripted([(529, headers(Retry_After="1"), b"")] * ATTEMPTS)
        with self.assertRaises(NotionError) as cm:
            list(api.search("page"))
        self.assertIn(f"after {ATTEMPTS} attempts", str(cm.exception))

    def test_a_client_error_is_loud_without_the_token_or_the_message(self):
        api = Scripted([(404, headers(), b'{"code": "object_not_found", "message": "Could not find block with ID: secret page title"}')])
        with self.assertRaises(NotionError) as cm:
            api.block("b1")
        self.assertEqual(str(cm.exception), "GET blocks/b1: HTTP 404 object_not_found")
        self.assertNotIn("secret", str(cm.exception))

    def test_a_network_error_names_only_the_request(self):
        api = Scripted([OSError("connection reset by peer")])
        with self.assertRaises(NotionError) as cm:
            api.block("b1")
        self.assertNotIn("secret-token", str(cm.exception))

    def test_broken_json_is_loud(self):
        api = Scripted([(200, headers(), b"<html>")])
        with self.assertRaises(NotionError):
            api.block("b1")

    def test_no_token_no_client(self):
        with self.assertRaises(NotionError):
            Notion("")


if __name__ == "__main__":
    unittest.main()
