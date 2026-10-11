# Stock-Insights — Trading Strategy Executive Brief

> A plain-English guide to how the trading engine works, why the approach is sound,
> and where it falls short. Written for a non-technical reader; jargon is explained inline.

---

## TL;DR (the one-line summary)

It's a **momentum-and-trend system for jumpy US stocks**. In plain terms: it hunts for
stocks that are *already going up*, are *moving more than the average stock*, and show
*no obvious reason to fall* — then buys a measured amount and gets out quickly if it's wrong.

It is a disciplined, rules-based **screener**, not a money-printing machine — and it is
currently **paper-traded (simulated)**, not connected to a real broker.

---

## The Recipe — 5 Steps

Think of it as a series of filters. A stock must survive **every** stage to get bought.

### 1. Pick the playing field
Start with the S&P 500 (America's 500 biggest companies) and keep only stocks that are:

| Filter | Threshold | Why |
| --- | --- | --- |
| Not a penny stock | Price ≥ $5 | Avoid junk |
| Easily tradable | ≥ $20M traded/day | Can get in and out cleanly |
| "Jumpy" enough | **Beta ≥ 1.3** | Momentum needs movement |

> **Beta** measures how much a stock swings compared to the overall market.
> Beta 1.0 = moves with the market; 1.3 = moves 30% *more*, both up **and** down.
> The system deliberately wants the twitchy ones because momentum strategies need
> movement to profit.

### 2. Rank them
Each survivor gets a score:

- **60%** — how jumpy it is (beta)
- **40%** — its 3-month price gain (momentum)
- **+ bonus** — if it's in a clear uptrend

The **top 75** make the shortlist.

### 3. Decide BUY / HOLD / SELL
The core buy rule — a stock must be in a **healthy uptrend**:

- ✅ Price is above its **50-day average**, *and* the 50-day average is above the
  200-day average (the classic "trend is up" signal)
- ✅ Positive momentum (gained ground over the last 1 **or** 3 months)
- ✅ **RSI between 45 and 70**

> **RSI (Relative Strength Index)** is a 0–100 "how hot is this stock" gauge.
> Below ~30 = beaten down; above ~70 = overbought / overheated.
> The 45–70 band means *"rising with room to run, but not yet frothy."*

| Situation | Decision |
| --- | --- |
| Price falls below 50-day average | **SELL** (trend broken) |
| Overbought (RSI ≥ 70) or momentum cooling | **HOLD** (wait) |
| Healthy uptrend + positive momentum + RSI 45–70 | **BUY** ✅ |

### 4. Demand extra confirmation (the quality gates)
A "BUY" is downgraded to "wait" unless it **also**:

- 📈 **Beats the S&P 500** over the last 3 months (relative strength — buy leaders, not laggards)
- 🔊 Trades on **above-average volume** (real interest, not a fluke)
- 🎯 Scores at least **75% conviction**

> **Conviction** is a simple tally of 5 yes/no health checks — trend, medium momentum,
> short momentum, healthy RSI, and no bad news — each worth 20%.
> So 75% ≈ **4 of the 5 boxes ticked**.

### 5. Sanity-check the wider world (the overlays)
Even a perfect setup gets held back or shrunk if:

| Condition | Action |
| --- | --- |
| **Earnings** within 3 days | Don't buy (too unpredictable) |
| Big **macro event** within 2 days (inflation data, Fed decision, jobs report) | Cut position size in half |
| **Strongly negative sentiment** on the stock | Don't buy (BUY → HOLD) |
| **Strong negative post-earnings gap** (already reported, sold off hard) | Don't buy — the drift is against you |
| Overall market is **"risk-off"** (fear high) | Don't buy |
| Market merely **"cautious"** | Buy half-size |

> **Post-earnings drift (PEAD)** is the one place an earnings *result* helps rather than
> blocks: once a stock **has** reported, a strong beat the market rewarded with a gap-up
> tends to keep drifting up for weeks (and a big miss keeps sinking). A positive drift
> *adds* to conviction; a negative one subtracts, and a strong down-gap vetoes a fresh buy.
> It only nudges an otherwise-valid setup — the technicals still lead.

---

## How Much Each Filter Counts (Weightings)

A common question: *"what's the weight of each filter?"* The honest answer is that the
system is **mostly a pass/fail checklist, not a weighted average.** Explicit weights
exist in only two places; everything else is a gate that must be cleared.

### Weighted place #1 — The ranking score
Decides *which* stocks make the 75-name shortlist and in what order. News plays **no
role** here.

| Ingredient | Weight |
| --- | --- |
| Beta (how jumpy the stock is) | **60%** |
| 3-month momentum (recent gain) | **40%** |
| Uptrend bonus | +0.5 (flat) |

### Weighted place #2 — The conviction score
Five independent health checks, each worth exactly **20%**. A stock needs **≥ 75%**
(i.e. **4 of 5**) to be eligible to buy.

| Check | Weight |
| --- | --- |
| Trend (price > 50-day avg, and 50-day > 200-day) | 20% |
| Medium-term momentum (3-month gain > 0) | 20% |
| Short-term momentum (1-month gain > 0) | 20% |
| Healthy RSI (between 45 and 70) | 20% |
| **News is not negative** | 20% |

> ⚠️ **Note on sentiment:** it can only ever *subtract* — positive sentiment doesn't add
> points. And because 75% = 4 of 5 checks, negative sentiment alone doesn't block a buy;
> it just **removes your margin for error** on the technicals. (The sentiment read itself
> comes from **StockTwits crowd mood**, reinforced by **Alpha Vantage** article-level news
> on the actionable names — see the sentiment note below.)
>
> **Post-earnings drift** adjusts conviction *outside* the 5 checks: a fresh positive drift
> adds points and a negative one subtracts, so a name can clear or miss the 75% bar on the
> strength of how it reacted to its last earnings report.

### Everything else — hard gates (no weight, just pass/fail)
Failing **any single one** of these blocks the buy entirely — so they behave like
"infinite weight":

- Price above 50-day average · 50-day above 200-day
- Positive momentum · RSI in 45–70
- Beats the S&P over 3 months (relative strength)
- Above-average trading volume
- Conviction ≥ 75%
- No earnings within 3 days
- Market regime not "risk-off"

---

## Worked Example — One Stock Through the Funnel

Let's walk **NVIDIA (NVDA)** — a genuinely high-beta chip stock — through the whole
process.

> 📌 The figures below are an **illustrative snapshot to show the mechanics** — they are
> *not* live market data or a recommendation.

**The raw numbers on the day (illustrative):**

| Metric | Value |
| --- | --- |
| Price | $170 |
| Beta | 1.6 (jumpy — qualifies) |
| ATR (typical daily swing) | $5 |
| 50-day avg | $150 · 200-day avg | $130 |
| RSI | 58 |
| 3-month gain | +18% (S&P did +6%) |
| Today's volume | above its 20-day average |

**Step 1 — Shortlist ranking:** high beta + strong momentum + uptrend bonus → lands
high on the 75-name shortlist. ✅

**Step 2 — Base signal:** price ($170) > 50-day ($150) > 200-day ($130), momentum
positive, RSI 58 sits in the healthy 45–70 band → **BUY candidate.** ✅

**Step 3 — Conviction score:**

| Check | Result | Points |
| --- | --- | --- |
| Trend | ✅ | 20% |
| 3-month momentum | ✅ | 20% |
| 1-month momentum | ✅ | 20% |
| Healthy RSI (58) | ✅ | 20% |
| News not negative *(neutral)* | ✅ | 20% |
| **Total** | | **100%** |

**Step 4 — Quality gates:** beats the S&P (+18% vs +6%) ✅ · volume above average ✅ ·
conviction 100% ≥ 75% ✅ → **survives as a BUY.**

**Step 5 — Overlays:** no earnings within 3 days ✅ · market regime "risk-on" ✅ · news
neutral ✅ → **BUY confirmed.**

**Position sizing** (£50,000 pot, ~1.27 £→$):

- Stop-loss = 2 × ATR below entry = $170 − $10 = **$160**
- Target = 2 × risk above entry = $170 + $20 = **$190** (2:1 reward-to-risk)
- *Risk-based limit:* £750 max loss ÷ $10 stop distance ≈ **95 shares**
- *Capital-slot limit:* (1/8 of pot) ÷ $170 ≈ **46 shares**
- **Buy the smaller → 46 shares** (position is *capital-bound*), ≈ $7,820,
  risking ≈ **£360** if the stop hits.

### Now change one thing: the news turns negative

Say the crowd turns **bearish** on NVDA (StockTwits tags skew negative, and — since NVDA
is an actionable name — the Alpha Vantage article-level read agrees). Two things happen:

1. **Conviction drops to 80%** (4 of 5 checks) — still above the 75% bar, so NVDA can
   *still* be bought. Sentiment alone didn't kill it.
2. **But the position gets shrunk.** The engine's "edge-weighted sizing" scales size by
   how far conviction clears the 75% floor:
   - At 100% conviction → full size (46 shares)
   - At 80% conviction → **~60% size (≈ 27 shares)** — smaller bet on a shakier setup.

And if sentiment were **strongly** negative (an "avoid" signal — the crowd very bearish,
or a bearish article-level read on the day's actionable names), the BUY would be
**downgraded to HOLD outright**, no matter how good the technicals looked.

> 💡 **Why crowd + article sentiment, not headline keywords.** The signal is driven by
> **StockTwits crowd mood** (what traders are actually saying — it reads market sentiment
> directly, without trying to parse a headline snippet), reinforced by **Alpha Vantage**
> article-level news on the actionable names (scored over the *full article*, so a stock
> merely mentioned doesn't skew it). Separately, a once-daily **Claude insight** gives a
> plain-English read of each name's headlines for context (display-only — it doesn't move
> the signal). A keyword scan survives only as a last-resort fallback.

---

## How Much It Buys — and How It Protects the Money

This is arguably the best-designed part of the whole system.

- 💰 **Pot:** £50,000 by default (adjustable via the `CAPITAL_GBP` setting), max **8 positions**
  at once (no single stock dominates). The cap counts everything you already hold — new BUYs only
  fill *free* slots, and the sector and total-risk limits below include your existing positions
  too. (A position flagged to sell keeps its slot until the sale fills.)
- 🛑 **Every trade risks at most 1.5% (£750).** It sizes each position so that *if the
  stop-loss triggers, the loss can't exceed £750.*

> A **stop-loss** is a pre-set exit price. Here it's placed **2× ATR** below entry.
> **ATR (Average True Range)** is the stock's typical daily price swing — so the stop
> respects each stock's natural volatility rather than using an arbitrary percentage.

- 🎯 **Target profit is 2× the risk** (risk £750 to make £1,500 — a "2:1 reward-to-risk")

**Portfolio-wide guardrails:**

- Max **3 stocks per sector**
- Total possible loss capped at **12% of the pot** (£6,000 if *every* stop hit at once)
- Won't buy two stocks that **move in lockstep** (avoids doubling-up on the same bet in disguise)

**Exits happen automatically** via:
- A **trailing stop** (locks in gains as the stock rises)
- A **trend break**
- A **time limit** (~15 days)

---

## Why This Is a Sound Approach ✅

1. **Built on a real, documented market effect.** Momentum — winners tend to keep
   winning for a while — is one of the most studied and persistent patterns in finance.
   This isn't astrology.
2. **Risk control is the star, not the stock-picking.** Fixed 1.5%-per-trade risk,
   portfolio heat caps, and hard stops mean no single bad call can blow up the account.
   Professionals will tell you *survival* matters more than *being right*.
3. **Disciplined and unemotional.** The rules fire the same way every day — no
   panic-selling, no chasing.
4. **Honest with itself.** Its backtest replays the exact live rules as one £50,000
   account on the S&P 500 *as it was at the time* (not today's winners), compares the
   result with simply holding the S&P 500 or cash, tests the entry signal against random
   entries, and models trading costs. That honesty is rare — and, as below, it's what
   shows the rules haven't beaten the index.

---

## Where It Falls Short ⚠️

### The headline result
**Replayed honestly, the rules trailed simply holding the index.** From October 2017 to
October 2026 the backtest turned **£50,000 into about £89,000 (6.6% a year)** — against
about **£175,000 (15.0% a year)** from holding the S&P 500 and **£62,500** from cash. The
entry signal does beat chance (about +0.12R per trade versus +0.06R for random entries on
the same stocks with the same exits), but the account was only ~44% invested on average,
held trades for about a week, and paid ~4% a year in trading costs — so the small edge per
trade never added up to market-beating returns. (Figures from the 2026-10-11 run; the app's
📈 tab always shows the latest.)

### Design / strategy limits
1. **It's not actually trading.** The "portfolio" and track record are simulated — the
   ledger resolves trades by replaying historical prices. Real-world fills, gaps, and
   slippage will be worse than modelled.
2. **Momentum's Achilles' heel: sharp reversals.** These strategies bleed money in
   choppy, directionless markets and get hit hard when a roaring market suddenly snaps
   (2018, early 2020, 2022). The regime filter *helps* but reacts after the fact — it
   can't dodge a surprise.
3. **High-beta cuts both ways.** The same jumpiness that fuels gains amplifies losses
   when it's wrong.

### Data / execution weaknesses
4. **The macro-event calendar is hard-coded** (manually typed-in dates). If those dates
   drift, it mis-times its safety size-downs — unless you pay for a data feed.
5. **Free, unofficial data source (Yahoo Finance).** It's flaky; extras like options
   data, short-interest, and even earnings dates are "best effort" and often silently
   missing — meaning some safety checks may quietly do nothing.
6. **The back-test still flatters reality a little:**
   - *Residual survivorship bias* — it now uses the S&P 500 as it was each month, including
     86 companies that later left the index, but ~110 former members have no free price
     history (bankruptcies, most takeovers), so some of the worst outcomes are missing.
   - The news / sentiment, earnings and macro-event layers can't be replayed (no free
     history), so those parts of the live system are untested.
7. **Coarse scoring.** Conviction is a blunt 5-point scale, and sentiment can only ever
   *hurt* a score, never help. The sentiment inputs are decent (StockTwits crowd mood +
   Alpha Vantage article-level news, with a Claude insight for colour), but they still feed
   a single binary "not negative" check rather than a graded contribution.

---

## Bottom Line — Is It a Good Way to Make Money?

**As a framework — yes, it's a legitimately well-built, professionally-minded one.**
It combines a genuine market edge (momentum) with the thing most amateur systems skip:
rigorous risk control. The discipline and self-honesty are genuinely above average.

**But temper expectations:**

- Momentum strategies are **feast-or-famine** — long stretches of steady gains
  punctuated by painful drawdowns. Expect stretches where it loses.
- Its edge is **modest and fragile** — dependent on flaky free data and a market that
  keeps trending. Real trading costs and taxes eat into it.
- **It hasn't beaten the index.** The point-in-time backtest returned 6.6% a year against
  15.0% for the S&P 500 — the bar any stock-picking system has to clear.
- **It has never been tested with real money.** Simulated results almost always beat
  live results.

### Recommendation
Treat it as a strong **decision-support tool and a well-disciplined paper-trading
system** — *not* a validated money-maker. Before risking real capital, get:

1. ✅ A reliable **paid data feed**
2. ✅ A **point-in-time back-test** — now built; so far it says the rules trail the index
3. ✅ A few months of **live paper-trading** matched against the back-test to confirm
   the edge survives contact with reality

---

<sub>This brief describes the strategy logic in the stock-insights codebase. It is an
educational summary, not financial advice.</sub>
