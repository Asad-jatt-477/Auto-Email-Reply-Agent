"""
dashboard/app.py - human-in-the-loop console for the Email Reply Agent.

  streamlit run dashboard/app.py

Review queue : escalated / low-confidence emails; edit the AI draft and send,
               or mark as handled. Sending removes the queue label in Gmail.
Decision log : every decision with filters; mark AI decisions correct or
               incorrect (this builds a real labelled dataset over time).
Metrics      : volume, action mix, automation rate, human-verified accuracy.

Reads the same SQLite DB as the agent (AGENT_DATA_DIR). Gmail is only
contacted when you press a button that needs it.
"""

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import config  # noqa: E402
from src.review_actions import approve_and_send, mark_handled, record_verdict  # noqa: E402
from src.step3_state import init_db, list_decisions  # noqa: E402

st.set_page_config(page_title="Email Agent Console", layout="wide")


@st.cache_resource(show_spinner=False)
def gmail_service():
    from src.step1_auth import get_gmail_service

    return get_gmail_service()


def gmail_link(thread_id):
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}" if thread_id else ""


def load(limit=2000) -> pd.DataFrame:
    rows = [r for r in list_decisions(limit=limit) if r["action"] not in ("sending", "human_reply")]
    return pd.DataFrame(rows)


init_db()
st.title("Email Agent Console")
st.caption(f"Database: {os.path.abspath(config.DB_PATH)}")

df = load()
tab_queue, tab_log, tab_metrics = st.tabs(["Review queue", "Decision log", "Metrics"])

with tab_queue:
    pending = df[df["review_status"] == "pending"] if not df.empty else df
    st.subheader(f"Waiting for a human: {len(pending)}")
    if pending.empty:
        st.info("Nothing to review.")
    for _, row in pending.iterrows():
        gid = row["gmail_id"]
        title = f"[{row['action'].upper()}] {row.get('subject') or '(no subject)'} - {row.get('sender') or ''}"
        with st.expander(title):
            st.write(f"**Why:** {row.get('reason') or '-'}")
            st.write(f"**Category:** {row.get('category') or '-'} | **Priority:** {row.get('priority') or '-'} | "
                     f"**Confidence:** {row.get('confidence') if pd.notna(row.get('confidence')) else '-'}")
            st.text_area("Email (first 300 characters)", row.get("snippet") or "", disabled=True, key=f"snip-{gid}")
            link = gmail_link(row.get("thread_id"))
            if link:
                st.markdown(f"[Open the thread in Gmail]({link})")
            draft = st.text_area("Reply", row.get("draft_text") or "", height=180, key=f"draft-{gid}")
            c1, c2 = st.columns(2)
            if c1.button("Send reply", key=f"send-{gid}", type="primary"):
                try:
                    approve_and_send(gmail_service(), gid, draft)
                    st.success("Reply sent.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Not sent: {exc}")
            if c2.button("Mark as handled", key=f"done-{gid}"):
                try:
                    mark_handled(gmail_service(), gid)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not update Gmail labels: {exc}")

with tab_log:
    if df.empty:
        st.info("No decisions yet - run the agent first.")
    else:
        actions = st.multiselect("Action", sorted(df["action"].unique()), default=sorted(df["action"].unique()))
        view = df[df["action"].isin(actions)]
        cols = ["timestamp", "action", "source", "category", "priority", "confidence", "language",
                "sender", "subject", "reason", "review_status"]
        st.dataframe(view[[c for c in cols if c in view.columns]], width="stretch", hide_index=True)

        st.markdown("**Verify AI decisions** (builds labelled data for evaluation)")
        unverified = view[~view["review_status"].isin(["correct", "incorrect", "approved_sent", "dismissed"])
                          & view["review_status"].ne("pending")]
        for _, row in unverified.head(20).iterrows():
            gid = row["gmail_id"]
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.write(f"{row['action'].upper()} - {row.get('subject') or ''} ({row.get('sender') or ''})")
            if c2.button("Correct", key=f"ok-{gid}"):
                record_verdict(gid, True)
                st.rerun()
            if c3.button("Wrong", key=f"bad-{gid}"):
                record_verdict(gid, False)
                st.rerun()
        st.download_button("Download decision log (CSV)", view.to_csv(index=False).encode("utf-8"),
                           "decisions.csv", "text/csv")

with tab_metrics:
    if df.empty:
        st.info("No data yet.")
    else:
        total = len(df)
        counts = df["action"].value_counts()
        reviewed = df[df["review_status"].isin(["correct", "incorrect"])]
        human_acc = (reviewed["review_status"] == "correct").mean() if len(reviewed) else None
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Emails processed", total)
        m2.metric("Auto-replied", f"{counts.get('reply', 0) / total:.0%}")
        m3.metric("Sent to a human", f"{(counts.get('escalate', 0) + counts.get('review', 0)) / total:.0%}")
        m4.metric("Human-verified accuracy", f"{human_acc:.0%} ({len(reviewed)})" if human_acc is not None else "-")
        st.bar_chart(counts)
        daily = df.assign(day=pd.to_datetime(df["timestamp"], utc=True).dt.date).groupby(["day", "action"]).size()
        st.line_chart(daily.unstack(fill_value=0))