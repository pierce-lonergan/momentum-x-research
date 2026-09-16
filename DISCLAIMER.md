# Disclaimer

## Not investment advice

Nothing in this repository is investment, financial, legal, or tax advice, and nothing in it is an
offer, solicitation, or recommendation to buy or sell any security or financial instrument. The
author is not a licensed investment adviser or broker-dealer. If you are making financial decisions,
consult a qualified professional.

## Not a performance claim

This repository contains **no verified track record**, and its own evidence argues against reading it
as one.

- All trading described here was conducted in a **paper (simulated) brokerage account**. No real
  capital was ever deployed by this system.
- The program's own conclusion, documented in [`docs/TARGET.md`](docs/TARGET.md) and
  [`docs/ATTEMPTS_LEDGER.md`](docs/ATTEMPTS_LEDGER.md), is that it produced **zero certified edges**
  across 39 hypothesis families, and that a passive index position outperformed it.
- Historical equity figures appearing in older documents are paper-account balances, and several are
  superseded. Where a figure was later found to be window-selected, mis-sourced, or otherwise wrong,
  the correction is recorded alongside it rather than quietly removed. **Read the corrections.**
- Simulated results are not indicative of real results. A paper broker fills orders in ways a real
  venue will not: this program measured its own paper environment filling orders up to 17.6x a
  minute's entire volume instantly at the quoted touch, and under-charging regulatory fees by ~1.44x.

## Historical documents are a record, not current guidance

The `docs/` tree is an append-mostly research log spanning hundreds of documents. Earlier documents
frequently contain claims that later documents refute — that is the point of the record. Any single
document may be out of date, superseded, or explicitly withdrawn. Where the repository's current
position matters, [`docs/TARGET.md`](docs/TARGET.md) and
[`docs/ATTEMPTS_LEDGER.md`](docs/ATTEMPTS_LEDGER.md) are the sources of truth.

Configuration values, position sizes, and risk parameters appearing in historical documents and
example commands **should not be copied**. Several were abandoned precisely because they were unsafe.

## No warranty

This software is provided "as is", without warranty of any kind, express or implied, including but
not limited to the warranties of merchantability, fitness for a particular purpose, and
noninfringement. See [LICENSE](LICENSE).

**Trading involves substantial risk of loss.** Automated trading systems can fail in ways that are
difficult to anticipate, including — as documented in this repository — silently unprotected
positions, configuration that does not match intent, and bookkeeping that disagrees with the broker.
If you run any part of this code against a live account, you do so entirely at your own risk.

## Credentials

No API credentials are included in this repository, and none should be added. Anything that touches
a broker or market-data vendor requires your own keys, supplied via environment variables. Bring
your own accounts and respect your vendors' terms of service — market data redistribution in
particular is usually restricted.

## Third-party data

Market data referenced in this repository was obtained under the author's own vendor agreements and
is **not** redistributed here. Analyses and derived statistics are published; the underlying vendor
data is not.
