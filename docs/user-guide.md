# Recon Engine — User Guide

### What happens when you click each button

A plain-language walk-through of every screen in the app — what you're looking at, what each button actually does, and where your data goes between the moment you upload a file and the moment a payment shows up matched.

*For the operations & finance team. No technical background required. Skip ahead any time — nothing here is sequential.*

---

## Contents

- [The big idea](#the-big-idea)
- [Getting around](#getting-around)
- [Command Center](#command-center)
- [Ingest files](#ingest-files)
- [Run reconciliation](#run-reconciliation)
- [Matching configuration](#matching-configuration)
- [Analyst queue](#analyst-queue)
- [AR Reconciliation](#ar-reconciliation)
- [Audit trail](#audit-trail)
- [A run's results: Summary, Matched, Exceptions](#a-runs-results-summary-matched-exceptions)
- [Run data tabs](#run-data-tabs)
- [Browsing live data: Gold data](#browsing-live-data-gold-data)
- [Switching & creating customers](#switching--creating-customers)
- [Architecture page](#architecture-page)
- [Common questions](#common-questions)
- [Glossary](#glossary)

---

## The big idea

Every screen in this guide is a view onto the same four-stage journey. Understand this once and every button in the app makes sense.

| Stage | What happens |
|---|---|
| **1 · Your file** | The Excel or PDF you upload is saved exactly as-is, byte for byte. Nothing is read yet. |
| **2 · Read (Silver)** | The app reads the file using that source's own column names — "CO6 No", "Party Name", exactly as your ERP calls them. |
| **3 · Translate (Gold)** | Every source's fields get translated into one shared vocabulary, so a bank statement and any ERP's export speak the same language. |
| **4 · Reconcile** | Bank credits are matched against bills by amount, date and reference number. What's left over becomes an exception to review. |

> **The one thing worth remembering**
>
> Stage 3 ("gold") is a **pool that keeps growing**. Every file you've ever ingested is still in there, together — nothing is ever deleted automatically. Stage 4 ("reconcile") is a **snapshot taken the moment you click Run** — it never updates itself afterwards. Every "why don't I see my new data" question in this guide traces back to one of those two facts. See [Common questions](#common-questions) for the two situations this actually causes.

Two more things worth knowing before you start clicking around:

- **Ingesting** (uploading files) and **reconciling** (matching bank credits to bills) are two separate steps, on two separate pages. Uploading a file never runs a match by itself — you always go and click "Run reconciliation" afterwards.
- Two kinds of tab look similar but behave completely differently: the **Gold data** tabs (in the left sidebar) are always live — open them any time and they show what's true right now. The **Reconciliation result** tabs (Summary, Matched, Exceptions, and the four "Run data" tabs) are a frozen photograph of one specific run and never change on their own.

---

## Getting around

The screen is always split the same way: a sidebar of pages on the left, the page content on the right.

**The sidebar** is grouped into *Operate* (upload files, run a match, tune the matching rules), *Workspace* (review and decide on exceptions, browse aging receivables, see the full history log), *Reconciliation result* (the outcome of whichever run you currently have open — greyed out until you've opened one), *Gold data* (always-live browsers for what's actually in the system), and *Platform* (a technical overview page). A badge number next to a Reconciliation-result item (e.g. "Matched · 29") is a live count from the run you currently have open.

**Customer switcher** — near the top of most pages sits a dropdown listing every customer this instance manages. Everything else on screen belongs to whichever customer is selected. Switching it clears whatever run you had open (a run belongs to one customer) and remembers your choice for next time. **"+ new customer"** (on the Ingest files page) creates a brand-new one, starting from a copy of the default customer's file-format choices and matching rules.

**Sharing a link** — the web address updates as you navigate, so copying it and sending it to a colleague reopens the exact same view for them.

---

## Command Center

The page you land on. A dashboard, not a workflow step — nothing here changes any data.

Five tiles show how many bills and credits are currently in the system, how many are matched, how many are waiting for review, and how many are still unresolved. A "largest open exceptions" shortlist lets you click any row to jump straight into the Analyst queue focused on it. A donut chart and four meters break down every bank credit into auto-matched / you-matched / needs-review / unmatched. A five-step pipeline strip (Ingest → Gold layer → Reconcile → Analyst review → Resolved) lights up as each stage has actually happened for this customer.

The two buttons on this page ("Ingest files", "Run reconciliation") are just shortcuts to those pages.

> **Behind the scenes:** everything on this page comes from one call the app makes for you automatically — you don't trigger it. It re-checks itself every time you upload a file or open a run.

---

## Ingest files

Where every Excel and PDF enters the system. This is **step 1** of the four-stage journey — it never runs a reconciliation by itself.

### Setting up each file slot

One row per document type: the bank statement, the bills export, and each lineage document (receipt notes, credit notes, and so on). Each row has a checkbox to include it, a dropdown to say which file format it is (this app supports several ERPs' export formats side by side), and an upload box.

Changing a format dropdown is a **permanent setting for this customer**, saved the moment you change it — it's not something you re-pick every time you upload. The next time you or a colleague opens this page, it's already set the way you left it.

**"+ add lineage source"** adds an extra document type beyond the two built-in ones; it's saved as soon as you add it, even before you've uploaded a file into it. "Remove" on a lineage row switches it off the same way.

### Clicking "Ingest files"

This is the button that actually sends your files in. It only turns on once at least one slot has a file attached. Here is exactly what happens to each file, in order:

1. **Saved (Bronze)** — The file is stored exactly as uploaded. If you accidentally upload the exact same file twice, the app recognises it (down to the byte) and doesn't store a second copy — you'll see it marked *deduped* instead of *registered*.
2. **Read (Silver)** — The file is opened and every row read into that source's own column names — nothing is renamed or reinterpreted yet. This as-read version is kept as the record of what the app actually saw in your file; the screens all read the translated version below.
3. **Translated (Gold)** — Those source-specific column names are translated into one shared set of names every part of the app uses — so it doesn't matter which ERP a bill came from, the matching engine sees the same shape either way.
4. **Merged in** — Each row is checked against everything already in the system. A bill that matches one already there (same bill number and same registration reference) has its details refreshed in place. A bill that's new goes in as a new row. **Nothing from a previous upload is ever removed** — new uploads add to the pool, they don't replace it.

Once it's done, you'll see a result panel: which files were registered vs. deduped, how many rows were newly added vs. updated vs. left untouched, and — if you included a bank statement — a line confirming the count and total the statement itself prints on its last page actually match what was read (this catches a corrupted or mis-scanned PDF immediately, before it can cause a bad match later).

> **An ingestion is exactly the files you attach.** A slot you leave empty (or untick) is skipped — nothing is ever filled in for you, so what lands in the system is only ever the documents you chose. If you attach nothing at all, the Ingest button stays off.

> **Behind the scenes:** "Same bill number and registration reference" is the default rule for recognising a bill you've already seen — it mirrors how a daily export naturally re-lists yesterday's bills alongside new ones. This is configurable per customer. A bill that's already been accepted as a confirmed match is protected: if a later file tries to change one of its numbers, that change is logged as a conflict instead of silently overwriting a settled payment.

### "All ingestions"

Opens a history table of every upload ever made for this customer — same result columns as above, so you can look back at what was uploaded when and what it did.

---

## Run reconciliation

Where you actually match bank credits against bills. This is **step 2** — it always runs against everything currently in the system, however many files you've uploaded so far.

### Choosing a statement

The dropdown lists every bank statement you've ingested, labelled with its file name, the date range it covers, and how many credits it contains. Only the credits in the *chosen* statement get matched in this run — the bills side, though, always considers every bill currently in the system, regardless of which upload it came from.

### Snapshot vs. Incremental

**Snapshot** is the simple one: a one-off comparison of this statement against every bill in the system right now. It doesn't remember anything between runs — run it again tomorrow and it starts fresh.

**Incremental** is the ongoing mode: confident matches get locked in permanently, and anything left unmatched is carried forward and re-tried on the next incremental run instead of being handed to you fresh every time. This is what feeds the [Analyst queue](#analyst-queue) and the decisions (Accept/Reject) you make there.

### Clicking "Run reconciliation"

The app compares every credit in the chosen statement against every bill currently in the system: amount has to agree first, then reference numbers and dates decide the confidence of the match. When it finishes, you're taken straight to the Summary tab of the new run.

> **This result is frozen the moment it's created.** Uploading more files afterwards, or changing the matching rules, will never change what this run's tabs show — they're a photograph of the data at the exact moment you clicked Run. If your data has since changed and you want an up-to-date view, run reconciliation again; that creates a brand-new, separate result.

**"⚙ Matching config"** opens the settings editor for this customer without leaving the page.

---

## Matching configuration

The rulebook the matching engine follows. Changes here apply to **every future run** for this customer — never to a run that's already finished.

| Section | What it controls |
|---|---|
| Amount & tolerance | Which fields hold the credit's amount and the bill's amount, and how many rupees of rounding difference to allow. |
| Date signal | Which date field to compare first, and a fallback if the first is missing, plus how many days apart still counts as a match. |
| Exact-match signals | Extra fields that must agree exactly (e.g. zone), each carrying its own weight toward the overall confidence score. |
| Eligibility | Which bill statuses count as "paid" or "awaiting payment" for matching purposes. |
| Other tunables | How many days ahead a bill can be "expected", batching (letting one credit settle several small bills at once), rounding, and when a receivable counts as overdue. |
| Terminology & guidance | The wording shown to your analysts next to an exception — you can rewrite the advice text without changing what triggers it. |

"Save configuration" only turns on once you've actually changed something. "Reset to defaults" reverts the form on screen — it doesn't save anything until you click Save afterwards.

---

## Analyst queue

Where a human makes the final call on anything the matching engine wasn't fully confident about. This is the only page in the app where accepting or rejecting a match actually happens.

**What's in this list:** every match made by an **incremental** run, across all of them — not just your most recent one. Filter by date, by confidence, or by status. Click any row to expand it and see exactly why the engine picked that pairing (amount, dates, reference numbers, side by side).

### The four buttons

| On a row that's | Button | What happens |
|---|---|---|
| OPEN | Accept | Locks the match in as confirmed. If several bills could plausibly be the right one, you can pick a specific candidate before accepting. |
| OPEN | Reject | Releases the credit and the bill(s) back into the pool for a future run to try again, and opens an unmatched-credit exception for it. |
| LOCKED | Unlock | Sends a confirmed match back to OPEN, in case it needs a second look. |
| REJECTED | Reopen | Undoes a rejection and reclaims both sides — unless something else has already been matched to them since. |

Every one of these buttons updates the whole list immediately afterwards, since rejecting or accepting a match also changes the exception list below it.

**The exceptions table underneath** lists every unmatched credit or bill, open or resolved, with which run first noticed it and which run (if any) resolved it. This part is read-only — it's a record, not something you act on directly.

---

## AR Reconciliation

A bill-first view of the same underlying story — built for "what's still owed to us and for how long", rather than "what got matched in this run".

Four headline figures (outstanding amount, received this month, overall match rate, amount overdue past 30 days), an ageing chart, and a bill-by-bill table you can filter to overdue / awaiting / in review / settled. This page is entirely read-only — clicking a settled row takes you to the Analyst queue to see the decision behind it, but every actual decision happens there, not here.

---

## Audit trail

The complete, unfiltered log — every upload, every run, every accept/reject, every settings change.

**Activity feed** is a flat, newest-first list — search, filter by who did it (a person vs. the system itself), how recently, and what category of event. **By record** groups the same events by what they happened to, so you can see one bill or one match's whole history in order. All the filtering happens instantly on your screen — it's not fetching anything new each time you narrow it down.

---

## A run's results: Summary, Matched, Exceptions

These three tabs only appear once you have a run open — either one you just ran, or one you reopened from history. Remember: **frozen at the moment that run finished.**

**Summary** — the headline numbers for this one run: how many credits came in, how many matched, how many landed on each side unmatched — count and rupee value for each. If the file this run used had a data conflict flagged during ingest, a warning banner says so here.

**Matched** — every pairing this run settled, with a confidence badge on each. Nothing to click or decide here — it's the record of what happened.

**Exception queue** — everything this run couldn't confidently settle: credits with no matching bill, bills with no matching credit, and matches the engine made but flagged for a human to double-check. Filter tabs split these three kinds apart. A flagged-for-review row expands to the same evidence view as the Analyst queue — with an "Accept this bill" shortcut and a "Decide in Analyst queue" link, both of which only work if this was an *incremental* run (a one-off snapshot run has nothing durable to accept into).

---

## Run data tabs

Bank statement, Bills, Bills + lineage, Recoveries — the four raw tables this specific run actually used, frozen exactly as they were at the time.

**Bank statement** and **Bills** are this run's input tables, unmodified. **Recoveries** is the deduction detail (tax, GST, damages) behind each bill's net amount. **Bills + lineage** is the richest one: one row per bill with every resubmission attempt combined, and expanding a row shows the full upstream trail — purchase order → receipt/goods-received note → registration.

Every table on this page has an "Export .xlsx" link at the top, giving you the whole workbook for this run to keep or share outside the app.

---

## Browsing live data: Gold data

The screens in the whole app that are **always live** — no run needs to be open, and they reflect this second's true state.

**Gold data** — the translated, unified pool: Bank transactions / Bills / Recoveries / Lineage documents, exactly what the matching engine sees. Every file you've ever ingested is in here together; an "Ingestion" dropdown narrows the view to one specific upload — and it shows everything that upload *reported*, including bills it refreshed rather than added, so a re-export of bills you already had still shows you its own rows.

---

## Switching & creating customers

If this instance handles more than one vendor or entity, each one is a fully separate customer — separate files, separate gold data, separate runs, separate settings.

The dropdown near the top of most pages switches which customer everything else on screen refers to. **"+ new customer"** (on the Ingest files page) sets one up as a copy of the default customer's file formats and matching rules, so it's ready to use immediately.

---

## Architecture page

A technical reference, for anyone curious how it fits together under the hood — not part of the day-to-day workflow. Six expandable panels — Sources, Storage, Matching engine, Ledger, Governance, Interface — each with a short description and a couple of live numbers.

---

## Common questions

**"I uploaded a new Bill Status file, but the Bills page still shows old data."**
Check which "Bills page" you're on. *Gold data → Bills* is always live — if it looks unchanged, the new file may not have actually been included in your last Ingest (double-check its checkbox and that a file was really attached to that slot, not left on a sample default). If instead you're looking at *Reconciliation result → Run data → Bills*, that's expected: that tab is frozen from whenever that run was created. Go to *Run reconciliation* and run it again — the new run's Bills tab will reflect everything you've uploaded.

**"The Gold data / a run's Bills table shows way more rows than I expected — is it duplicating data?"**
Almost certainly not a duplicate — this is the "gold keeps growing" behaviour by design. Every file you've ever ingested for this customer is still in the pool together. Use the *Ingestion* filter dropdown on the Gold data page to narrow the view down to just one upload and confirm its row count matches what you expect.

**"Ingest failed with an error message I don't understand."**
Two common causes: the file you attached to a slot isn't actually in the format that slot's dropdown expects — check the file-format dropdown matches what you're uploading. Or, less often, you're re-uploading a file that was already fully ingested before — this is safe to do and shouldn't error; if it does, that's worth reporting rather than working around.

**"I changed the matching rules — why didn't my old run's numbers change?"**
By design: a run is a frozen snapshot of both the data *and* the rules at the moment it ran. A settings change only affects runs you create *after* saving it.

**"Where do I actually approve or reject a match?"**
Only one place: the *Analyst queue*. The Exception queue on a run's results has a shortcut into it, but the decision itself always happens on the Analyst queue page — and only for matches made by an incremental run.

---

## Glossary

| Term | Meaning |
|---|---|
| Bronze | Your file, saved exactly as uploaded — the untouched original. |
| Silver | The same data, read into rows and columns, but still using the source file's own field names. |
| Gold | The same data again, translated into one shared vocabulary every part of the app understands. |
| Snapshot run | A one-off reconciliation: this statement vs. everything currently in gold, with nothing remembered afterwards. |
| Incremental run | An ongoing reconciliation: confirmed matches lock in permanently, unmatched items carry forward to the next run. |
| OPEN / LOCKED / REJECTED | A match's status in the Analyst queue: awaiting a decision, confirmed, or turned down. |
| Exception | A credit or bill that couldn't be confidently matched — either side of the ledger, kept visible until resolved. |
| MATCH_REVIEW | The engine did find a pairing, but wants a human to confirm it before it's treated as settled. |
| CO6 / CO7 | Reference numbers issued as a bill moves through the railway payment system — CO6 is the bill's registration, CO7 the payment order. |
| RNOTE / CRN | Upstream documents (a goods-receipt note, and a credit/challan note) that let a payment be traced back to the purchase order it originated from. |
| Entity key | The pair of reference numbers used to recognise "this is the same bill I already have" across two different file uploads. |

---

*Recon Engine — internal user guide. Screens and wording may evolve; the four-stage journey in [The big idea](#the-big-idea) is the stable mental model underneath all of it.*
