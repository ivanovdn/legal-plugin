# Legal Triage — using the assistant

**Who this is for:** the legal team, once the assistant is installed. Getting it
onto your laptop is a separate guide — [`docs/tester-setup.md`](tester-setup.md).

**The short version:** work on real contracts the way you normally would, and
press ⚑ whenever the assistant is wrong. That's the whole job.

---

## What you'll see in the panel

Three tabs: **Findings**, **Chat**, **Preferences**.

### Findings

**Review this contract** reads the open document and triages it. Expect **30–60
seconds** — the model runs on our own hardware, so it's slower than the chat
assistants you're used to, and it is thinking about your whole contract.

You'll get a summary, a **Red and Missing Context** card (the blocking items),
per-clause findings, and **Business Questions**. On each finding:

- **Click the clause name** to jump to that clause in the document.
- **Comment in doc** adds a Word comment at that clause.
- **Accept redline** applies the suggested wording as a tracked change. It only
  appears when the finding quoted the exact current wording — when it's absent,
  the suggested wording is there for you to apply by hand.

### Chat

Ask anything about the open document — *"who signs this?"*, *"is the liability
cap our standard?"*, *"redline the termination notice to 60 days"*.

If you ask for a change, the answer comes back as a card with:

- **Apply with Track Changes** — makes the edit as a normal Word tracked change,
  so you can Accept or Reject it the usual way.
- **Go to** — shows you where it would land, without changing anything.
- **Discard** — throws the suggestion away. Nothing in the document changes.

### Finalize → clean copy

At the bottom of the panel. It **accepts every tracked change in the document —
not only the assistant's** — and turns Track Changes off, so you can send a clean
copy. It asks you to confirm first. **Save a copy before you use it** if you want
to keep the redlined version, then use Word's **File → Save As** for the clean one.

### Three notices worth recognising

| Notice | What it means |
|---|---|
| **"This document isn't saved yet."** (blue) | Save the file. Until you do, the review history, the chat and any feedback you send stop being connected to this contract the moment you close it. |
| **"Part of this document was not sent"** (red) | The contract was too long to send whole, so the review is incomplete. Tell us when you see this. |
| **"Condense earlier turns"** (a button, in a long chat) | Room is running out, and earlier chat is crowding out the contract itself. Clicking it shortens the earlier conversation. It usually happens on its own. |

### A tip about redline colours

Tracked insertions and deletions appear in **Word's own revision colours**, which
by default make both the same colour. If you'd rather have green insertions and
red deletions:

- **Windows:** **Review → Track Changes** (the small arrow / **Advanced Track
  Changes Options**) → set **Insertions: Green**, **Deletions: Red**.
- **Mac:** **Word → Preferences → Track Changes** → same two settings.

That's a setting on your own machine, not something the assistant controls.

---

## Telling us what it got wrong

This is the part of the pilot that matters most. **Work on real contracts the way
you normally would** — the point is to find where the assistant is wrong, not to
exercise every button.

**The ⚑ button** sits on every finding, every proposed edit and every chat reply.
Use it whenever something is wrong: a finding that isn't a real problem, an edit
that changes the wrong thing, an answer that's incorrect. One sentence is plenty
— *"this clause is fine as written"*, *"wrong field"*, *"we never require this"*.

**The "Send feedback" button at the top** is for what the assistant **missed** —
*"it didn't flag the assignment clause"*. Please use it. A missed issue leaves no
trace anywhere; if you don't tell us, nothing else can.

**You don't need to report anything else.** Which edits you Apply and which you
Discard is already recorded, so a Discard is itself a signal. Just work normally.

**What gets sent:** your note, the text of the document you're working on, and
the specific finding or edit you flagged. Your name is attached.

**Where it goes:** to the development team, so we can reproduce the exact case.
**It does not train the assistant** — flagging something will not change its
behaviour tomorrow. It gets the problem fixed properly instead.

> **Save your document before you close it.** On an unsaved document, Word can't
> store the id that ties your feedback to that contract, so reports from a
> closed-unsaved draft can't be grouped with later ones. The panel tells you when
> this applies — the blue *"This document isn't saved yet"* notice above the tabs.
> Save the file and it clears. No notice means you're fine.

---
