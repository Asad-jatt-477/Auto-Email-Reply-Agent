"""In-memory fake of the Gmail API surface the agent uses, with failure injection."""

import base64
import itertools
from email import message_from_bytes
from email.policy import default as default_policy


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    def __init__(self):
        self.messages = {}          # id -> gmail message resource
        self.label_store = {}       # id -> name
        self.sent = []              # parsed EmailMessage objects
        self.drafts = []
        self.fail = {}              # op name -> remaining failures (int) ; -1 = always
        self.calls = []
        self._ids = itertools.count(1)
        self.watch_calls = 0

    # -- helpers for tests ------------------------------------------------
    def add_message(self, msg_id, from_="customer@example.com", subject="Hello", body="Hi there, a question.",
                    labels=("INBOX", "UNREAD", "CATEGORY_PERSONAL"), headers=None, html=False, thread_id=None):
        mime = "text/html" if html else "text/plain"
        hdrs = [{"name": "From", "value": from_}, {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{msg_id}@mail.example.com>"},
                {"name": "Content-Type", "value": f"{mime}; charset=utf-8"}]
        for k, v in (headers or {}).items():
            hdrs.append({"name": k, "value": v})
        self.messages[msg_id] = {
            "id": msg_id, "threadId": thread_id or f"t-{msg_id}", "labelIds": list(labels),
            "internalDate": str(1700000000000 + len(self.messages)), "snippet": body[:50],
            "payload": {"mimeType": mime, "headers": hdrs,
                        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}},
        }

    def label_names(self, msg_id):
        names = []
        for lid in self.messages[msg_id]["labelIds"]:
            names.append(self.label_store.get(lid, lid))
        return names

    def _maybe_fail(self, op):
        self.calls.append(op)
        left = self.fail.get(op, 0)
        if left:
            if left > 0:
                self.fail[op] = left - 1
            raise RuntimeError(f"injected failure: {op}")

    # -- API surface -------------------------------------------------------
    def users(self):
        return _UsersAPI(self)


class _UsersAPI:
    def __init__(self, g):
        self.g = g

    def messages(self):
        return _Messages(self.g)

    def labels(self):
        return _Labels(self.g)

    def drafts(self):
        return _Drafts(self.g)

    def watch(self, userId, body):
        def run():
            self.g._maybe_fail("watch")
            self.g.watch_calls += 1
            return {"historyId": "1", "expiration": "1900000000000"}
        return _Exec(run)

    def stop(self, userId):
        return _Exec(lambda: {})


class _Messages:
    def __init__(self, g):
        self.g = g

    def list(self, userId, q="", maxResults=100, pageToken=None):
        def run():
            self.g._maybe_fail("list")
            terms = q.split()
            out = []
            for m in self.g.messages.values():
                names = {self.g.label_store.get(l, l).lower() for l in m["labelIds"]}
                ok = True
                for t in terms:
                    if t == "is:unread" and "unread" not in names:
                        ok = False
                    elif t == "in:inbox" and "inbox" not in names:
                        ok = False
                    elif t.startswith("-label:") and t[7:] in {n.replace(" ", "-") for n in names}:
                        ok = False
                if ok:
                    out.append({"id": m["id"], "threadId": m["threadId"]})
            start = int(pageToken or 0)
            page = out[start:start + maxResults]
            resp = {"messages": page} if page else {}
            if start + maxResults < len(out):
                resp["nextPageToken"] = str(start + maxResults)
            return resp
        return _Exec(run)

    def get(self, userId, id, format="full"):
        def run():
            self.g._maybe_fail("get")
            return self.g.messages[id]
        return _Exec(run)

    def modify(self, userId, id, body):
        def run():
            self.g._maybe_fail("modify")
            labels = self.g.messages[id]["labelIds"]
            for lid in body.get("removeLabelIds", []):
                if lid in labels:
                    labels.remove(lid)
            for lid in body.get("addLabelIds", []):
                if lid not in labels:
                    labels.append(lid)
            return {"id": id}
        return _Exec(run)

    def send(self, userId, body):
        def run():
            self.g._maybe_fail("send")
            raw = base64.urlsafe_b64decode(body["raw"])
            msg = message_from_bytes(raw, policy=default_policy)
            self.g.sent.append({"msg": msg, "threadId": body.get("threadId"), "raw": raw})
            return {"id": f"sent-{next(self.g._ids)}"}
        return _Exec(run)


class _Labels:
    def __init__(self, g):
        self.g = g

    def list(self, userId):
        def run():
            self.g._maybe_fail("labels.list")
            return {"labels": [{"id": i, "name": n} for i, n in self.g.label_store.items()]}
        return _Exec(run)

    def create(self, userId, body):
        def run():
            self.g._maybe_fail("labels.create")
            lid = f"Label_{next(self.g._ids)}"
            self.g.label_store[lid] = body["name"]
            return {"id": lid, "name": body["name"]}
        return _Exec(run)


class _Drafts:
    def __init__(self, g):
        self.g = g

    def create(self, userId, body):
        def run():
            self.g._maybe_fail("drafts.create")
            did = f"draft-{next(self.g._ids)}"
            self.g.drafts.append({"id": did, **body})
            return {"id": did}
        return _Exec(run)

    def delete(self, userId, id):
        def run():
            self.g._maybe_fail("drafts.delete")
            self.g.drafts = [d for d in self.g.drafts if d["id"] != id]
            return {}
        return _Exec(run)