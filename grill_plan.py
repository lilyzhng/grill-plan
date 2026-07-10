#!/usr/bin/env python3
"""grill-plan: Google-Docs-style threaded comments on a markdown doc, with an agent bridge.

Usage:
    python3 grill_plan.py <doc.md> [--port 7788] [--threads <path.json>] [--no-open]

- Threads persist to a JSON file (default: sessions/<doc-stem>.threads.json next to this script).
- The browser UI (static/index.html) lets a human read the doc, see anchored threads,
  and reply inside each thread.
- The AGENT BRIDGE is the threads file itself: an agent replies by appending a comment
  to the JSON file. The server watches the file's mtime and pushes changes to the
  browser over SSE, so replies appear live without a Discord/webhook hop.

Threads file schema:
{
  "doc": "/abs/path/to/doc.md",
  "threads": [
    {
      "id": "t-<uuid>",
      "label": "Grill 1",
      "anchor": "exact phrase copied from the doc",
      "status": "open" | "resolved",
      "comments": [
        {"author": "Jackie", "text": "...", "ts": "2026-07-10T00:45:00+00:00"}
      ]
    }
  ]
}
"""

import argparse
import copy
import json
import mimetypes
import os
import queue
import threading
import time
import uuid
import webbrowser
from urllib.parse import unquote
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
STATIC_DIR = SKILL_DIR / "static"
WATCH_INTERVAL_S = 1.0


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ThreadStore:
    """Owns the threads JSON file. Thread-safe. Broadcasts mutations to SSE subscribers."""

    def __init__(self, path, doc_path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.subscribers = set()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"doc": str(doc_path), "threads": []}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_locked()
        self._mtime = self.path.stat().st_mtime

    # -- persistence ---------------------------------------------------------

    def _persist_locked(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime

    def check_external_change(self):
        """Reload if another process (the agent) edited the file. Returns True if reloaded."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return False
        with self.lock:
            if mtime == self._mtime:
                return False
            try:
                self.data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                return False  # mid-write; next tick will catch it
            self._mtime = mtime
        self.broadcast()
        return True

    # -- reads ----------------------------------------------------------------

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def find_locked(self, thread_id):
        for t in self.data["threads"]:
            if t["id"] == thread_id:
                return t
        return None

    # -- mutations -------------------------------------------------------------

    def create_thread(self, anchor, author, text, label=None):
        if not anchor or not text:
            raise ValueError("anchor and text are required")
        with self.lock:
            thread = {
                "id": "t-" + uuid.uuid4().hex[:8],
                "label": label or f"{author} {sum(1 for t in self.data['threads'] if t['comments'] and t['comments'][0]['author'] == author) + 1}",
                "anchor": anchor,
                "status": "open",
                "comments": [{"author": author or "anon", "text": text, "ts": now_iso()}],
            }
            self.data["threads"].append(thread)
            self._persist_locked()
        self.broadcast()
        return thread

    def add_comment(self, thread_id, author, text):
        if not text:
            raise ValueError("text is required")
        with self.lock:
            thread = self.find_locked(thread_id)
            if thread is None:
                raise KeyError(thread_id)
            comment = {"author": author or "anon", "text": text, "ts": now_iso()}
            thread["comments"].append(comment)
            self._persist_locked()
        self.broadcast()
        return comment

    def set_status(self, thread_id, status):
        if status not in ("open", "resolved"):
            raise ValueError("status must be open|resolved")
        with self.lock:
            thread = self.find_locked(thread_id)
            if thread is None:
                raise KeyError(thread_id)
            thread["status"] = status
            self._persist_locked()
        self.broadcast()
        return thread

    # -- SSE --------------------------------------------------------------------

    def subscribe(self):
        q = queue.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self.subscribers.discard(q)

    def broadcast(self, kind="threads"):
        for q in list(self.subscribers):
            q.put(kind)


def make_handler(store, doc_path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass  # keep stdout clean for the launcher

        # -- helpers --

        def send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw)

        # -- GET --

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                html = (STATIC_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif self.path == "/api/doc":
                md = Path(doc_path).read_text()
                title = next((l.lstrip("# ").strip() for l in md.splitlines() if l.startswith("# ")), Path(doc_path).name)
                self.send_json({"markdown": md, "title": title, "path": str(doc_path)})
            elif self.path == "/api/threads":
                self.send_json(store.snapshot())
            elif self.path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                q = store.subscribe()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            kind = q.get(timeout=15)
                            self.wfile.write(f"data: {kind}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    store.unsubscribe(q)
            else:
                self.serve_doc_asset()

        def serve_doc_asset(self):
            """Serve files (images, html) referenced by the doc, relative to the doc's directory."""
            rel = unquote(self.path.lstrip("/").split("?")[0])
            base = Path(doc_path).parent.resolve()
            target = (base / rel).resolve()
            if not (target.is_file() and str(target).startswith(str(base) + os.sep)):
                self.send_json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # -- POST --

        def do_POST(self):
            try:
                body = self.read_body()
                if self.path == "/api/doc":
                    md = body.get("markdown")
                    if not isinstance(md, str) or not md.strip():
                        raise ValueError("non-empty markdown required")
                    Path(doc_path).write_text(md)
                    store.broadcast("doc")
                    self.send_json({"ok": True})
                elif self.path == "/api/threads":
                    thread = store.create_thread(
                        anchor=body.get("anchor", ""),
                        author=body.get("author", "anon"),
                        text=body.get("text", ""),
                        label=body.get("label"),
                    )
                    self.send_json({"thread": thread})
                elif self.path == "/api/comment":
                    comment = store.add_comment(body.get("thread_id", ""), body.get("author", "anon"), body.get("text", ""))
                    self.send_json({"comment": comment})
                elif self.path == "/api/resolve":
                    thread = store.set_status(body.get("thread_id", ""), body.get("status", "resolved"))
                    self.send_json({"thread": thread})
                else:
                    self.send_json({"error": "not found"}, 404)
            except KeyError as e:
                self.send_json({"error": f"thread not found: {e}"}, 404)
            except (ValueError, json.JSONDecodeError) as e:
                self.send_json({"error": str(e)}, 400)

    return Handler


def watch_file(store, stop_event):
    while not stop_event.is_set():
        store.check_external_change()
        stop_event.wait(WATCH_INTERVAL_S)


def serve(doc_path, threads_path=None, port=7788, open_browser=True):
    doc_path = Path(doc_path).resolve()
    if threads_path is None:
        threads_path = SKILL_DIR / "sessions" / (doc_path.stem + ".threads.json")
    store = ThreadStore(threads_path, doc_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, doc_path))
    actual_port = server.server_address[1]
    stop_event = threading.Event()
    watcher = threading.Thread(target=watch_file, args=(store, stop_event), daemon=True)
    watcher.start()
    url = f"http://127.0.0.1:{actual_port}"
    print(f"grill-plan serving {doc_path.name} at {url}")
    print(f"threads file (agent bridge): {threads_path}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
    return store


def main():
    ap = argparse.ArgumentParser(description="Threaded comments on a markdown doc")
    ap.add_argument("doc", help="path to markdown document")
    ap.add_argument("--port", type=int, default=7788)
    ap.add_argument("--threads", default=None, help="threads JSON path (agent bridge file)")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    serve(args.doc, args.threads, args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
