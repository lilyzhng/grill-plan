"""Tests for grill_doc server. Runs with unittest or pytest.

    python3 -m unittest tests.test_grill_plan -v   (from skill dir)
"""
import json
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import grill_plan  # noqa: E402


def http(url, body=None):
    req = urllib.request.Request(url, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    else:
        data = None
    try:
        with urllib.request.urlopen(req, data=data) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class GrillDocTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.doc = Path(cls.tmp.name) / "doc.md"
        cls.doc.write_text("# Test Doc\n\nThe quick brown fox jumps over the lazy dog.\n")
        cls.threads_path = Path(cls.tmp.name) / "t.threads.json"
        cls.store = grill_plan.ThreadStore(cls.threads_path, cls.doc)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), grill_plan.make_handler(cls.store, cls.doc))
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.stop_event = threading.Event()
        threading.Thread(target=grill_plan.watch_file, args=(cls.store, cls.stop_event), daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.stop_event.set()
        cls.server.shutdown()
        cls.tmp.cleanup()

    # -- happy path ------------------------------------------------------------

    def test_01_get_doc(self):
        status, body = http(self.base + "/api/doc")
        self.assertEqual(status, 200)
        self.assertEqual(body["title"], "Test Doc")
        self.assertIn("quick brown fox", body["markdown"])

    def test_02_create_thread_and_comment(self):
        status, body = http(self.base + "/api/threads", {
            "anchor": "quick brown fox", "author": "Jackie", "text": "why fox?", "label": "Grill 1",
        })
        self.assertEqual(status, 200)
        tid = body["thread"]["id"]
        self.assertEqual(body["thread"]["label"], "Grill 1")
        self.assertEqual(body["thread"]["status"], "open")

        status, body = http(self.base + "/api/comment", {"thread_id": tid, "author": "Lily", "text": "because speed"})
        self.assertEqual(status, 200)
        self.assertEqual(body["comment"]["author"], "Lily")

        status, body = http(self.base + "/api/threads")
        self.assertEqual(status, 200)
        thread = next(t for t in body["threads"] if t["id"] == tid)
        self.assertEqual(len(thread["comments"]), 2)
        self.assertEqual(thread["comments"][1]["text"], "because speed")

        # persisted to disk
        on_disk = json.loads(self.threads_path.read_text())
        self.assertEqual(len(next(t for t in on_disk["threads"] if t["id"] == tid)["comments"]), 2)

    def test_03_resolve_and_reopen(self):
        _, body = http(self.base + "/api/threads", {"anchor": "lazy dog", "author": "Jackie", "text": "why lazy?"})
        tid = body["thread"]["id"]
        status, body = http(self.base + "/api/resolve", {"thread_id": tid, "status": "resolved"})
        self.assertEqual(status, 200)
        self.assertEqual(body["thread"]["status"], "resolved")
        status, body = http(self.base + "/api/resolve", {"thread_id": tid, "status": "open"})
        self.assertEqual(body["thread"]["status"], "open")

    # -- agent bridge (external file edit) ----------------------------------------

    def test_04_external_edit_reloads(self):
        _, body = http(self.base + "/api/threads", {"anchor": "jumps over", "author": "Jackie", "text": "grill q"})
        tid = body["thread"]["id"]
        time.sleep(1.1)  # ensure distinct mtime tick
        on_disk = json.loads(self.threads_path.read_text())
        for t in on_disk["threads"]:
            if t["id"] == tid:
                t["comments"].append({"author": "Jackie", "text": "agent reply via file", "ts": grill_plan.now_iso()})
        self.threads_path.write_text(json.dumps(on_disk))
        deadline = time.time() + 5
        found = False
        while time.time() < deadline and not found:
            _, body = http(self.base + "/api/threads")
            thread = next(t for t in body["threads"] if t["id"] == tid)
            found = any(c["text"] == "agent reply via file" for c in thread["comments"])
            if not found:
                time.sleep(0.3)
        self.assertTrue(found, "server did not pick up external file edit")

    # -- errors / edge cases -----------------------------------------------------

    def test_05_missing_fields(self):
        status, body = http(self.base + "/api/threads", {"anchor": "", "author": "x", "text": ""})
        self.assertEqual(status, 400)
        status, body = http(self.base + "/api/comment", {"thread_id": "t-nope", "author": "x", "text": "hi"})
        self.assertEqual(status, 404)
        status, body = http(self.base + "/api/comment", {"thread_id": "t-nope", "author": "x", "text": ""})
        self.assertEqual(status, 400)
        status, body = http(self.base + "/api/resolve", {"thread_id": "t-nope", "status": "weird"})
        self.assertEqual(status, 400)

    def test_06_unknown_route(self):
        status, _ = http(self.base + "/api/nope")
        self.assertEqual(status, 404)

    def test_08_doc_asset_serving(self):
        figures = Path(self.tmp.name) / "figures"
        figures.mkdir(exist_ok=True)
        (figures / "x.png").write_bytes(b"\x89PNG fake")
        req = urllib.request.Request(self.base + "/figures/x.png")
        with urllib.request.urlopen(req) as res:
            self.assertEqual(res.status, 200)
            self.assertEqual(res.headers["Content-Type"], "image/png")
            self.assertEqual(res.read(), b"\x89PNG fake")

    def test_09_asset_traversal_blocked(self):
        with TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("nope")
            rel = "/../" + Path(outside).name + "/secret.txt"
            status, _ = http(self.base + rel)
            self.assertEqual(status, 404)
        status, _ = http(self.base + "/%2e%2e/%2e%2e/etc/hosts")
        self.assertEqual(status, 404)

    def test_07_unicode_roundtrip(self):
        _, body = http(self.base + "/api/threads", {"anchor": "Test Doc", "author": "Jackie", "text": "中文批注 ≥ 0.8 测试"})
        tid = body["thread"]["id"]
        _, body = http(self.base + "/api/threads")
        thread = next(t for t in body["threads"] if t["id"] == tid)
        self.assertEqual(thread["comments"][0]["text"], "中文批注 ≥ 0.8 测试")


    def test_10_edit_doc(self):
        status, body = http(self.base + "/api/doc", {"markdown": "# Edited Doc\n\nnew body text.\n"})
        self.assertEqual(status, 200)
        self.assertEqual(self.doc.read_text(), "# Edited Doc\n\nnew body text.\n")
        status, body = http(self.base + "/api/doc")
        self.assertEqual(body["title"], "Edited Doc")
        status, _ = http(self.base + "/api/doc", {"markdown": "   "})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
