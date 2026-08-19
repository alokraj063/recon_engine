# Advanced parameters

Six tunables control the reconciliation. They fall into three groups: two
shape **expectation** (which bills should have been paid in this
statement), two shape **matching** (which credit pairs with which bill,
and how confidently), and two govern the **batched fallback** (one credit
covering several bills). Defaults live in `backend/recon/config.py`; the
UI exposes all six under *Advanced options*, applied on the next run.

A useful fact about this data, established from the sample statement:
**advised bills settle same-day** — every credit matched via an advice
date had a gap of 0 days. The natural lag for bills that settle straight
off a CO7 (before the export shows an advice) is about **5–6 days**.
Several recommendations below follow from those two numbers.

---

## Expectation knobs — who belongs on the "missing payment" list

### `window_days` (default 0)

How far either side of the statement's own dates a bill's **payment
advice date** may sit and still count as "expected in this statement."
Money cannot arrive before IREPS advises the bank, so for a single-day
statement the right value is 0: the advice must fall on the statement
date itself.

**Adapt when:** the statement covers several days (set roughly to the
span). **Do not** widen it to "catch more" — a bill advised last week was
settled by last week's statement, and pulling it in here reports an
already-paid bill as a false shortfall. This parameter never affects
matching; it only sizes the BILL ONLY denominator.

### `co7_lookback_days` (default 5)

How long a `CO7 DONE` bill with **no advice yet** stays on the "money may
arrive any day" watchlist. The status pipeline is
CO7 → advice (= status flips to PAYMENT MADE) → credit, but the export is
a snapshot that lags reality: credits do land while the export still says
CO7 DONE. This knob decides how far back such CO7s are still *expected*.

**Adapt when:** you want a wider or narrower radar. In the sample,
moving 5 → 7 added 21 unadvised CO7s (dated 2 days earlier) to BILL ONLY
as `CO7_ISSUED_NO_ADVICE` — including a bill that was otherwise invisible
(not matched, not expected, reported nowhere). Too short creates that
blind spot; too long piles CO7s that settled via earlier statements into
the queue as noise. 5–7 fits the observed CO7→credit lag. Like
`window_days`, it never changes matching results.

---

## Matching knobs — who pairs with whom, and how confidently

### `date_tolerance_days` (default 2)

Once a credit and bill already agree on **amount**, this decides whether
their **dates** agree: `|credit value date − bill date| ≤ tolerance`,
where the bill date is its advice date, or CO7 date if unadvised. A date
match via advice scores 4, via CO7 1, zone 2 — and the confidence label
reads the same signals (zone + advice date = HIGH; zone + CO7 date =
MEDIUM; one = LOW; neither = AMOUNT_ONLY).

**Adapt when:** CO7-fallback matches are landing as LOW/AMBIGUOUS and
their gaps look genuine. Advised bills settle same-day here, so the knob
is irrelevant for them; it only bites on CO7-dated cases. On the sample,
2 → 5 upgraded one LOW to MEDIUM; **6** would also have resolved the
AMBIGUOUS tie toward the more plausible bill (CO7 six days before the
credit) over one with a stale 27-day-old advice. Every extra day dilutes
what "dates agree" certifies — much beyond the natural lag and the date
signal stops separating right bills from wrong ones. It also widens the
batched pass's date fence (below).

It cannot add or remove candidates and cannot change what's expected —
counts stay put; labels and tie-breaks move.

### `amount_tolerance` (default 0.00)

The candidate gate itself: a bill enters scoring only if its Net Amt
equals the credit within this many rupees (both sides rounded to the
paisa first). 0 = exact match.

**Adapt when:** legitimate credits miss their bills by small rounding
differences — IREPS rounds Net Amt to whole rupees, so ₹1 is a defensible
setting if exact matching leaves explainable BANK ONLY residue. Every
rupee of slack grows each credit's candidate pool and with it the chance
of coincidental amount collisions (more AMBIGUOUS ties), so keep it as
tight as the data allows.

---

## Batched-payment knobs — one credit, several bills

### `allow_batched` (default on)

Enables the third matching pass: for credits no single bill explains,
look for a small set of bills — same zone, dates within
`date_tolerance_days` — whose Net Amts **sum** to the credit (±₹0.50,
covering IREPS's whole-rupee rounding). Hits are labelled `BATCHED` and
flagged "verify before posting"; they also appear in MATCH REVIEW.
Switch off if you'd rather investigate combined remittances by hand:
those credits then fall to BANK ONLY and their bills to BILL ONLY.

### `max_batch_size` (default 3)

The cap on how many bills one credit may cover. Two forces argue for
keeping it small: cost grows combinatorially with size, and false
positives grow faster — the more numbers you may add together, the easier
it is to hit any target by coincidence. A 2-bill sum landing within 50
paise is strong evidence; a 6-bill sum is close to numerology. Raise it
only if a zone is known to remit many bills in one NEFT, and verify every
such match against the advice before posting.

---

## Quick reference

| Parameter | Default | Governs | Raise it when | Cost of raising |
|---|---|---|---|---|
| `window_days` | 0 | expectation (advice-dated bills) | multi-day statement | already-settled bills reported as shortfalls |
| `co7_lookback_days` | 5 | expectation (unadvised CO7s) | CO7→credit lag exceeds it | stale CO7s clutter BILL ONLY |
| `date_tolerance_days` | 2 | match scoring & tie-breaks | genuine CO7-lag matches stuck at LOW | date signal stops meaning anything |
| `amount_tolerance` | 0.00 | candidate admission | rounding leaves explainable misses | more amount collisions → more AMBIGUOUS |
| `allow_batched` | on | subset-sum fallback | — (turn off to review by hand) | combined credits go unexplained |
| `max_batch_size` | 3 | batch fallback size | zones remit many bills per NEFT | coincidental sums pass as matches |
