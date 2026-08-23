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
| **Negative news** on the stock | Don't buy |
| Overall market is **"risk-off"** (fear high) | Don't buy |
| Market merely **"cautious"** | Buy half-size |

---

## How Much It Buys — and How It Protects the Money

This is arguably the best-designed part of the whole system.

- 💰 **Fixed pot:** £50,000, max **8 positions** at once (no single stock dominates)
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
4. **Honest with itself.** It back-tests over 10 years, runs *Monte-Carlo* simulations
   (reshuffling trade history 1,000 times to see the *range* of plausible outcomes,
   not just the lucky path), checks out-of-sample stability, and models trading costs
   and slippage. That intellectual honesty is rare and a genuine strength.

---

## Where It Falls Short ⚠️

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
6. **The back-test flatters reality in two ways:**
   - *Survivorship bias* — it tests **today's** S&P 500 winners over 10 years, ignoring
     companies that failed and dropped out. This makes history look rosier than it was.
   - The back-test uses **only** the technical signals, while the *live* system also
     applies news and macro filters — so the tested edge and the real behaviour aren't
     the same thing.
7. **Coarse scoring.** Conviction is a blunt 5-point scale, and news can only ever
   *hurt* a score, never help.

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
- **It has never been tested with real money.** Simulated results almost always beat
  live results.

### Recommendation
Treat it as a strong **decision-support tool and a well-disciplined paper-trading
system** — *not* a validated money-maker. Before risking real capital, get:

1. ✅ A reliable **paid data feed**
2. ✅ A **survivorship-bias-free back-test**
3. ✅ A few months of **live paper-trading** matched against the back-test to confirm
   the edge survives contact with reality

---

<sub>This brief describes the strategy logic in the stock-insights codebase. It is an
educational summary, not financial advice.</sub>
