# MoMTrans v4 — Frontier Research Extension: Gaps Beyond the Prior Report

**Audience.** Senior quant engineer at JPM, single RTX 5070 (12 GB, sm_120), 20k labeled rows + 60k unlabeled, 4 tier specialists, current Spearman ceiling ≈ 0.14–0.16. The prior report covered architecture (TabPFNv2, TabM, CORN, SCARF, Muon, conformal, Mixup, etc.). This extension is organized around the **10 themes** the prior report did not address, plus a closing synthesis of the highest-leverage bets.

---

## TL;DR (3 bullets)

- **The orthogonal axis is data, not architecture.** Your Spearman ceiling is far more likely a *feature ceiling* than a *model ceiling*. The single highest-EV move is to ingest tick-level Polygon `trades_v1` and build microstructure features — VPIN, Cont-Kukanov-Stoikov multi-level OFI, Kyle's λ, Hawkes self-excitation, dark-pool/TRF prints, and Benzinga-derived catalyst embeddings (FinBERT/FinGPT). Mature quant-fund research, peer-reviewed microstructure literature, and the entire 2021 Optiver/2023 Optiver-Close/2024 Jane Street Kaggle pipeline confirms that order-flow features dominate over architectural sophistication on small horizons.
- **Validation, capacity, and execution are existential — not optimization.** Combinatorial Purged CV (López de Prado), Deflated Sharpe Ratio (Bailey & López de Prado 2014), Probability of Backtest Overfitting, and the Bouchaud square-root impact law (Maitrier-Loeper-Kanazawa-Bouchaud 2025 "double square-root") are non-negotiable if you intend to go from Spearman 0.16 to a tradeable strategy at $5M AUM. ELITE's Spearman of 0.004 is *prima facie* a multiple-testing artifact and must be tested under DSR/PBO before any capital is allocated.
- **The 5 highest-leverage NEW research bets** (in priority order) are: (1) tick-level microstructure feature pack (VPIN+OFI+Kyle's λ+Hawkes); (2) Benzinga FinBERT/FinGPT catalyst embeddings as auxiliary features and as MTL targets; (3) IQN/QR-DQN distributional-RL sizing replacing post-hoc Kelly to handle fat tails directly; (4) CPCV + Deflated Sharpe replacing 16-fold walk-forward — this almost certainly kills ELITE and may rescue VETOED; (5) Numerai-style feature neutralization to remove sector/beta exposure that is currently inflating CV correlations. Two further bets — symbolic alpha mining (alphagen / warm-start GP) and graph-based sympathy networks — are *speculative but cheap*.

---

## Key Findings

The prior report's 54 hand-engineered features and a 500K-parameter TabTransformer per tier are operating against an information ceiling that is structural to the *feature set*, not the architecture. The Numerai community's experience (Spearman ceilings of ~0.02–0.04 per era on a fully-engineered, neutralized, market-neutral target) suggests your 0.14–0.16 is already excellent — but it is also **likely contaminated by sector beta and survivorship bias** that CPCV+DSR will partially deflate. The asymmetric upside is in adjacent-data ingestion (tick + news + cross-ticker graph) where 2024–2026 papers consistently report 5–20× R² lifts on intraday horizons. Almost no published academic work targets *microcap intraday gap-up continuation* specifically; this is genuinely frontier territory and you should expect to do original work.

---

## 1. Feature Engineering on Unused Data Modalities

This is the single largest underexploited axis. Below, "complexity 1–5" is implementation difficulty, "lift" is honest expected Spearman impact based on adjacent-domain evidence.

### 1A. Microstructure features from tick-level `trades_v1`

**VPIN (Volume-Synchronized Probability of Informed Trading)** — Easley, López de Prado & O'Hara, *Review of Financial Studies* (2012); high-frequency variant in Easley et al. (2011). VPIN buckets trades by *volume* rather than clock time, applies bulk-volume classification (BVC) to split into buy/sell, and computes |Buy−Sell|/Volume per bucket. Real flash-crash precedent (May 6, 2010 alarm hours in advance). **Modern caveat**: Chakrabarty, Pascual & Shkilko (*JFM* 2015) showed BVC is materially worse than tick-rule (TR) and Lee-Ready (LR) for equities — TR/LR misclassification is 7.4–19% lower. **For your stack**: compute *both* TR-VPIN and BVC-VPIN (since you have full tick history, TR is feasible). Empirical evidence for predicting *intraday continuation*: Kang, Kim & Kim (KOSPI 200, 2020) and Abad-Yagüe (Spanish equities) both show VPIN ↑ → realized vol ↑ at 30-min to 4-hour horizons. **Fit for MoMTrans**: Strong. Compute a VPIN feature for the 9:30–10:00 window (your gap formation window) and the prior-day end-of-day VPIN. Complexity 3/5; expected Spearman lift 0.01–0.03.

**Multi-Level Order Flow Imbalance (MLOFI)** — Cont, Kukanov & Stoikov, *Journal of Financial Econometrics* (2014) for level-1; Xu, Gould & Howison (arXiv 1907.06230, 2019) for multi-level extension; **Kolm, Turiel, Westray** *Mathematical Finance* (2023) "Deep Order Flow Imbalance" for the deep-learning version on 115 Nasdaq stocks. Cont et al. found a single-stock R²≈65% on price changes from level-1 OFI alone; Kolm et al. demonstrate that *models trained on OFI significantly outperform models trained on raw order books* — this is critical because it means you do NOT need to feed the LOB to a transformer; you can collapse it to OFI features and use your existing tabular pipeline. With **only `trades_v1`** (no NBBO yet) you can compute signed trade flow via Lee-Ready or tick rule and a "trade OFI" proxy (Kolm calls this "trade flow imbalance"). **Lucchese, Pakkanen & Veraart** (*Int. J. Forecasting*, 2024) "Short-term predictability in order book markets" reinforces this on a deep-learning lens. Complexity 3/5; expected lift 0.02–0.04 (highest single-feature ROI in this section).

**Kyle's λ** — Kyle (*Econometrica* 1985) defines λ as the price-impact-per-unit-signed-volume. Estimable via per-stock OLS of Δp on signed Q over 5-min buckets. Modern foundations: Kyle-Obizhaeva (*Econometrica* 2016) "Market Microstructure Invariance" predicts λ ∝ (σ²/V)^(2/3) — gives you a single scalar per name per day capturing how much your gap should impact price. **Especially relevant for microcap**, where λ is 10–100× large-cap values. Complexity 3/5; lift 0.005–0.02.

**Hawkes process features for trade arrival** — Bacry & Muzy (arXiv 1301.1135, 2013, *Hawkes Process for Price and Trades*); Fabre & Muni Toke (arXiv 2401.09361, 2024 *Neural Hawkes*) for non-parametric high-D estimation; Jain, Firoozye, Kochems & Treleaven (*Finance Research Letters*, 2024) on compound Hawkes for LOB. The output you want is *self-excitation strength* α and *kernel decay* β per name during the morning gap. High α/β indicates herd-driven gap (likely to continue) vs. orderly news priced-in (mean reversion). Complexity 4/5 (need physics-informed-NN-style estimation or `tick` Python package); lift 0.005–0.015. Honest assessment: **borderline cost-effective** for your scale; only pursue if VPIN/OFI underwhelm.

**Trade sign classification (Lee-Ready vs BVC vs ML)** — Fedenia, Ronen & Nam (*Review of Quantitative Finance and Accounting*, 2024) showed ML-based trade-sign classification outperforms LR/TR on corporate bonds. For equities, evidence is that **LR ≥ TR ≫ BVC** (Chakrabarty et al. 2015). Implement LR with the modern O'Hara-Yao adjustment for sub-second timestamps. Complexity 2/5; this is plumbing for the rest of microstructure features.

**Sweep detection / multi-exchange sweeps** — Detect bursts where a single market participant sweeps across the NBBO at multiple exchanges in <1 second. Strong signal for institutional urgency; rarely studied academically but heavily used in retail momentum trading. **No formal benchmark** — fully speculative; complexity 3/5.

**Dark-pool / FINRA TRF prints (exchange code 4)** — TRF prints are off-exchange; large prints (>10k shares for microcaps) often signal institutional accumulation. Buti, Rindi & Werner (*Financial Management* 2022) "Diving into Dark Pools" shows TRF/dark share has stable explanatory power for next-period returns in cross-section. For microcaps, anomalous TRF print at 9:30–10:00 with positive sign is folk-evidence of accumulation. Complexity 1/5 once `trades_v1` is loaded. Honest caveat: **for microcaps, TRF can also be retail wholesaler internalization (Citadel/Virtu) which has the *opposite* informational sign** — make this a feature with both magnitude and a sign-disambiguation interaction.

**Microstructure invariance (Kyle-Obizhaeva 2016)** — predicts a universal scaling λ·V/σ ≈ const. As a feature, the *deviation* from invariance scaling is a regime indicator (especially for tier-jumping behavior in microcaps). Complexity 4/5; speculative but theoretically grounded.

### 1B. NBBO quote-based features from `quotes_v1`

(Note: you don't currently subscribe to `quotes_v1` per the brief. If you add it, the highest-ROI features are below; if not, derive proxies from `trades_v1`.)

- **Effective spread, realized spread, price impact** — standard microstructure decomposition; complexity 2/5.
- **Quote-to-trade ratio (QTR)** — ratio of NBBO updates to trades per minute; high QTR is a quote-stuffing/HFT signal; Hasbrouck & Saar evidence that high QTR predicts short-term volatility. Complexity 2/5.
- **Quote slope / book slope** — derivative of cumulative depth vs. price away from mid. Folklore: slope steepening predicts mean-reversion. Limited public evidence for microcaps.
- **Spoofing detection** — Fabre & Challet (arXiv 2504.15908, 2025) "Learning the Spoofability of Limit Order Books With Interpretable Probabilistic Neural Networks" provides a 2025 LOB spoofing model. ~31% of large orders in their crypto data could spoof; equities much lower. Mostly informational for microcap (spoofing on illiquid names is regulated heavily); complexity 5/5; **not recommended at your scale**.

### 1C. News / text features (Benzinga API + LLM)

- **FinBERT** (Araci 2019) and **FinGPT** (Wang, Yang, Wang 2023; Liu, Wang, Yang, Zha arXiv 2307.10485) are the standard 2024-2026 baselines for financial sentiment. The 2024 paper Halouškova & Lyócsa (arXiv 2503.19767) "Forecasting U.S. equity market volatility with attention and sentiment" specifically uses Benzinga-style news tone for vol forecasting. The MDPI 2024 paper using FinBERT+GPT-4 on the Nigerian All-Share Index found FinBERT+LSTM outperformed standalone LSTM and ARIMA. **For MoMTrans**: at minimum, embed each Benzinga headline within the d0 morning window with FinBERT, project to 16-dim with a learned linear head, average if multiple headlines, concat to your 54 features. Complexity 2/5; lift estimate 0.01–0.03 (catalyst-driven gaps especially).
- **LLM-based catalyst quality scoring** — Use an open-weights model (Llama-3.1-8B or Qwen-2.5-7B-Instruct) with a few-shot prompt to classify catalysts into FDA / earnings / secondary offering / sympathy / contract / management-change / dilution-risk / etc. Output a one-hot taxonomy + a "quality score". This is what Numerai-tier teams do informally. Complexity 3/5; lift 0.01–0.02; can run offline.
- **News velocity** — number of articles in trailing 60 min normalized by 30-day baseline; sentiment dispersion across articles. Complexity 1/5.
- **FinGPT v3 / DeepSeek-style sentiment-RL** — Liu et al. (arXiv 2412.10823, 2024) "FinGPT: Enhancing Sentiment-Based Stock Movement Prediction with Dissemination-Aware and Context-Enriched LLMs" is the current 2024–2025 frontier; uses news *propagation* features. The FinAI 2025 contest (FinRL-DeepSeek for stock trading) integrates LLM-generated sentiment scores directly into CVaR-PPO action scaling. Borderline for your stack; complexity 4/5.

### 1D. Cross-ticker / graph features ("sympathy stocks")

- **Sector contagion / co-movement networks** — Cucuringu, Li & Zhang (arXiv 2505.08180, 2025) on intraday volume forecasting with graphs; Cartea et al. (2023) statistical-arbitrage SPONGEsym graph clustering with reported 12.2% annualized / Sharpe 1.1 (pre-cost) on S&P 500.
- **Lead-lag DTW relations** — multiple 2024 papers (Inter-Intra GNN, *Expert Systems with Applications* 2025) use Dynamic Time Warping to extract lead-lag pairs and feed into GNNs.
- **Implicit causality GNN (DSF-GNN)** — Liao et al. (*Information* 2024) and the MDPI 2024 paper combine Granger causality with GNNs.
- **Hybrid Transformer-GNN for correlation forecasting** (arXiv 2601.04602, 2025) reports correlation MAE improvements out-of-sample 2019–2024.
- **For MoMTrans practically**: maintain a daily-updated *peer matrix* (k=20 nearest neighbors by 60-day return correlation, sector match, market-cap bucket); for each candidate, compute features over peers (peer mean gap, peer mean d-1 return, peer dispersion). This gives ~5–10 powerful tabular features without a full GNN. Complexity 2/5; lift 0.01–0.03. **Highest-ROI graph approach for your scale**.

### 1E. Short interest / borrow rate features

- Days-to-cover (FINRA twice-monthly), short % float, **borrow fee rate** (intraday from Interactive Brokers / S3 / Ortex), short utilization. The literature on **lender squeezes vs. market squeezes** (recent *Journal of Banking and Finance* paper, Bessembinder-Kalcheva-Le-Lemmon "How prevalent are short squeezes" 2025) finds short squeezes happen on ~9.9% of US stocks per quarter (market-driven). For microcap gap-ups specifically, borrow fee dynamics are predictive — fee jumps from 2% to 50% APR in days are **strong leading indicator of squeeze potential**. Complexity 2/5 (data collection is the bottleneck — Polygon does not provide; need IBKR/S3/Ortex); lift 0.02–0.04 *for the squeeze subset*.

### 1F. Options-implied features

For microcaps that *have* options (typically the higher-tier candidates), even crude options data is valuable: P/C volume ratio, 30-day implied vol, IV skew (put 25Δ vs. call 25Δ), max pain, gamma exposure. **Without an options subscription**, you can scrape ORATS or use yfinance daily snapshot — sufficient for daily features. Complexity 2/5; lift on ELITE/HIGH tier 0.01–0.03.

---

## 2. Position Sizing / Kelly / RL — Replace Post-hoc Kelly

Your current "Aggressive Kelly" applied post-hoc to model probabilities is the **single biggest theoretical risk** in MoMTrans. It assumes (a) the predicted probability is calibrated (likely it isn't — the prior report mentioned conformal but conformal coverage on autocorrelated finance data is unreliable; see §8C), (b) returns are i.i.d. Bernoulli (false; fat-tailed), and (c) you can trade fractional shares without slippage. UPenn 2023 (Beggy thesis) confirmed full Kelly *bankrupted in 100% of sports-betting simulations*; 0.5×Kelly with a 10% edge threshold delivered ~80% annual returns over 11 years. The same caution applies to microcaps.

### 2A. Joint learning of probability and bet size

- **Differentiable Sharpe ratio** — Moody & Saffell (1998 NIPS) "Direct reinforcement of trading systems" is the foundational paper; modern reincarnation in Mei et al. *NeurIPS* 2022 and Zhang, Zohren & Roberts (*JFDS* 2020). Replace your two-stage (predict P, then size) with end-to-end optimization of differentiable Sharpe over your model outputs. Complexity 3/5; expected lift on Sharpe 0.2–0.4.
- **Drawdown-constrained Kelly / Sortino objective** — Cover universal portfolios and modern updates by Pinelis & Ruppert.

### 2B. Reinforcement learning for sizing

- **Distributional RL** — *Implicit Quantile Networks (IQN)* — Dabney, Ostrovski, Silver, Munos (ICML 2018); *QR-DQN*, *C51* — Bellemare, Dabney, Munos (ICML 2017). For finance: Riedmiller et al. C51/QR-DQN/IQN on natural gas futures (arXiv 2501.04421, 2025) — **C51 outperformed classical RL by >32%**, IQN+CVaR target showed adjustable risk aversion. Risk Preference Adaptive Distributional RL (RPADiRL, *Applied Soft Computing* 2025) explicitly uses IQN for stock trading. For microcap fat tails this is the *correct* RL family; PPO/SAC ignore tail asymmetry.
- **CVaR-PPO** — Tail-Safe Hedging (arXiv 2510.04555, 2025) IQN+CVaR-PPO for derivatives; FinRL-DeepSeek 2025 contest applies CVaR-PPO to stock trading with LLM-augmented sentiment. **For your problem** the cleanest design: train an IQN whose output quantiles are the predicted 5-day return distribution; size = optimal Kelly of CVaR_α distribution where α is a tunable risk aversion. Complexity 4/5; expected lift on Sharpe 0.3–0.6.
- **Conservative Offline RL (CQL, IQL)** — Kumar, Zhou, Tucker, Levine (NeurIPS 2020 CQL; Kostrikov, Nair, Levine ICLR 2022 IQL). Critical for backtested data: standard PPO is *on-policy* and will collapse on offline backtests. For 20k labeled samples, CQL+IQN+CVaR is the *theoretically-correct* sizer. Implementation complexity 5/5; this is genuinely research-frontier for microcap.

### 2C. Risk-aware objectives

- **CVaR / Expected shortfall** — Rockafellar-Uryasev (2000); modern: Hu et al. (arXiv 2603.09734, 2026) "Long-Run CVaR RL" for dynamic systems. Treat ELITE positives as a fat-tail problem; train a separate quantile regression head that outputs the 5%-CVaR of 5-day return, size aggressive only when CVaR is positive.
- **Robust Kelly under uncertainty** — Pinelis & Ruppert 2023+ on Bayesian Kelly under edge uncertainty; gives you an honest fractional-Kelly multiplier as a function of credible-interval width.
- **Drawdown-constrained Kelly** — Busseti, Ryu & Boyd (2016) Risk-Constrained Kelly Gambling.

### 2D. Multi-armed bandit framing

- **Contextual bandits with continuous action** — appropriate when daily candidates are 30–100 names and you must select-and-size. Thompson sampling with linear-Gaussian posterior on edge gives you principled exploration. Cheap to implement (complexity 2/5). **Honest caveat**: bandits assume independent arms — on gap-up days with high cross-correlation this is violated. Use a *batched* contextual bandit (Linear UCB with covariance correction).

---

## 3. Walk-Forward Validation / Leakage / Purging — **Critical**

**This is the section the prior report most underweighted.** Your 16-fold walk-forward CV has an unknown probability of overfitting because you have not deflated for the implicit number-of-trials in feature selection / hyperparameter tuning / threshold tuning. **Until CPCV+DSR+PBO are run, the Spearman 0.16 ceiling is potentially a mirage.**

### 3A. Combinatorial Purged Cross-Validation (CPCV)

López de Prado *Advances in Financial Machine Learning* (Wiley 2018), Ch. 7. Generates ⁽ᴺ_ₖ⁾ train/test combinations from N groups choosing k as test, with **purging** (drop training samples whose label window overlaps the test window) and **embargo** (drop training samples within a buffer after the test window). For your 5-day forward labels, embargo ≥5 trading days. Yields a *distribution* of Sharpes — not a point estimate. Arian, Norouzi & Seco (*Knowledge-Based Systems* 2024 / SSRN 4778909) ran a synthetic-controlled comparison and confirmed **CPCV produces lower PBO and higher DSR than walk-forward**. Implementations: `timeseriescv` (sam31415), `mlfinlab`. Complexity 3/5. **Probably mandatory before any production deployment.**

### 3B. Purging and embargo

- Purging: drop overlapping-window labels.
- Embargo: span ≥ label horizon × (1 + autocorrelation persistence). For 5-day labels with positive autocorrelation, embargo 7–10 days.
- For microcaps: **also embargo around earnings, FDA dates, secondary-offering dates** to avoid event-conditional leakage.

### 3C. Deflated Sharpe Ratio (DSR)

Bailey & López de Prado, *Journal of Portfolio Management* (2014) 40(5):94–107; SSRN 2460551. DSR adjusts the observed Sharpe by (a) the variance of trial Sharpes (selection bias), (b) skew/kurtosis (non-normality), (c) the *number of independent trials*. Output: probability that the true Sharpe exceeds a benchmark. **For ELITE with ~85 positives/fold, the DSR is almost certainly going to be insignificant** — this is your honest prior. Complexity 2/5; mandatory.

### 3D. Probability of Backtest Overfitting (PBO)

Bailey, Borwein, López de Prado & Zhu (*Journal of Computational Finance* 2016, 20(4):39–69; SSRN 2326253). Combinatorial Symmetric Cross-Validation: split trials into IS/OOS halves, ask whether the IS-best strategy is *median-or-better* OOS. PBO = fraction of splits where IS-winner underperforms median OOS. PBO > 0.5 means your selection process is anti-predictive. **Run this on your 4-tier ensemble** before claiming the cascade works.

### 3E. White's Reality Check / Hansen's SPA / Romano-Wolf

- White (*Econometrica* 2000); Hansen *Test for Superior Predictive Ability* (*JBES* 2005); Romano-Wolf stepwise (*Econometrica* 2005). Standard family for testing whether the best of M strategies outperforms a benchmark *after* multiple-testing correction. Complexity 3/5.

### 3F. Recent 2024–2026 work

- Wang & Hyndman (Monash WP-20-2024) "Online conformal inference for multi-step time series" — directly relevant for your conformal usage on autocorrelated 5-day labels.
- Auer et al. ICML 2023 "Conformal Prediction for Time Series with Modern Hopfield Networks" (HopCPT) — uses Hopfield retrieval to find similar regimes for non-exchangeable conformal calibration. Strong fit for finance. Complexity 4/5.
- Temporal Conformal Prediction (arXiv 2507.05470, 2025) — Robbins-Monro online calibration for finance with abrupt regime shifts.

### 3G. Time-series holdout strategies

For a label horizon h with autocorrelation ρ, the effective sample size n_eff ≈ n·(1−ρ)/(1+ρ); with h=5 and ρ≈0.3, n_eff is ~half of n. Report this in any DSR computation.

---

## 4. Adjacent-Domain Transfer

### 4A. Medical small-data ML

- **Federated meta-learning for rare disease** — Chen et al. arXiv 2112.14364 (2021) DFML uses Inaccuracy-Focused Meta-Learning. Direct analogy: ELITE is your "rare disease" with 85 positives. The technique transfers as **MAML-style meta-learning across the 4 tiers**, with the inner loop adapting on whichever tier has labels for that fold.
- **Survival analysis with deep learning (DeepProg / DeepSurv)** — Katzman et al. (*BMC Med Research Methodology* 2018). Reframe gap continuation as a **time-to-failure** problem: at minute t, predict hazard rate of "gap fades below entry". Gives you a Cox-proportional-hazards regularization that naturally handles censoring (positions closed at end-of-window). Complexity 4/5; speculative.
- **Synthetic tabular data under federated learning (AML case)** — Isasa et al. (*JMIR* 2025) on synthetic tabular data fidelity tradeoffs in rare-disease federated settings. Same considerations apply if you SMOTE/CTGAN-augment ELITE positives — be very honest about the fidelity-utility tradeoff.

### 4B. Sports betting / handicapping

- **Closing Line Value (CLV) as truth proxy** — In sports betting, the closing line is the consensus market estimate; consistently beating CLV predicts long-term edge. **Direct analog for MoMTrans**: the *end-of-day price* on day d is the market's best estimate of the d-to-d+5 distribution. Compute "Closing Print Value" — your model's prediction vs. realized return at d+5 — this is an *in-sample* edge proxy that doesn't require profit attribution. Folklore in sports betting; rarely formalized in finance.
- **Walsh, Joshi & Boya** (*Machine Learning with Applications*, 2024) — NBA betting calibration > accuracy: "Kelly betting only works with a well-calibrated model." Cite this **directly to the tier-cascade design**; calibration is more important than threshold accuracy.
- **UPenn Wharton 2023 Beggy** — fractional Kelly (0.5×) with 10% edge threshold = ~80% annualized over 11 NBA seasons; full Kelly = 100% bankruptcy.
- **Bayesian models with Stan/PyMC for sports** — Direct transfer to a per-tier Bayesian binomial model whose posterior gives you credible intervals on edge for fractional-Kelly.

### 4C. Online ad-CTR ranking

- **DCN-V2** — Wang, Shivanna, Cheng, Jain, Lin, Hong & Chi (*WWW* 2021; arXiv 2008.13535). Cross-network learns explicit polynomial feature interactions via residual cross-layers; low-rank decomposition for compute. **Direct fit for MoMTrans**: replace one of your transformer blocks with a DCN-V2 cross-stack (~50K params); cross terms will discover interactions like volume×spread×short-interest that the transformer probably already learns implicitly. Worth a $2 ablation. Complexity 2/5; expected lift 0.005–0.015.
- **MMoE / PLE / STEM** — Ma et al. (KDD 2018) MMoE; Tang et al. (RecSys 2020) PLE; Su et al. 2023 STEM. **Direct fit**: replace your 4 independent tier-specialists with a single MMoE/PLE that shares experts across tiers and gates per-tier. Mitigates negative transfer between BROAD↔ELITE. Strong evidence in industrial recommendation that PLE > MMoE > shared-bottom. Complexity 3/5; expected lift on ELITE specifically (where data starvation is worst) 0.01–0.03.
- **Calibration in extreme imbalance** — Platt scaling, isotonic regression, beta calibration; see Niculescu-Mizil & Caruana 2005 baseline. The 2024 Frontiers in AI paper "credit card fraud detection with class imbalance" reviews focal loss, SMOTE, ADASYN, and cost-sensitive learning at <1% positive rates — directly applicable to ELITE.
- **Position bias correction** — only relevant if you're building a ranker; if you're outputting a per-name probability, this is moot.
- **Counterfactual evaluation** (Inverse Propensity Weighting, Doubly Robust) — Dudik et al. (ICML 2011); for your problem, you have *no propensities* (you don't randomize entries), so this is less directly applicable. However, for the decision policy ("entered vs. not entered"), DR-IPW could honestly measure expected value of taking trades you skipped historically.

### 4D. Extreme-imbalance fraud detection

- **2019–2024 fraud detection systematic reviews** — arXiv 2502.00201 (108 studies); Frontiers in AI 2025 (Bhattacharyya et al. perspective). Methods that consistently win: focal loss > class-weighted CE > SMOTE-based oversampling for tabular; CNN-LSTM hybrids for sequence. **For ELITE specifically**: focal loss with γ=2, α matched to base rate, plus *anomaly-detection auxiliary loss* (DeepSVDD or one-class SVM on negatives) — this is the standard 2024 fraud playbook.
- **Concept drift** — fraud detection literature has the cleanest treatments of regime change (ADWIN, DDM, EDDM). For markets, regime detection via change-point on VIX or your own VPIN works similarly.

---

## 5. Leaked / Discussed Quant Fund Techniques

Be careful — much of what circulates as "Renaissance/Two Sigma technique" is folklore. The genuinely sourceable items are below.

### 5A. Two Sigma

- Halite I/II/III competition (2016–2018) — multi-agent RL platform; not financial-modeling per se but their open-source infrastructure shows their commitment to RL. Their Kaggle "Financial Modeling Challenge" 2017 — the 5th-place writeup (Best Fitting team) revealed: GBDT ensembles dominated, custom median averaging beat mean, exponential weighted means did *not* improve CV. **Lesson for you**: gradient boosting still wins on small tabular finance data with fewer features than yours. Test LightGBM/CatBoost as a first-stage ensemble member.

### 5B. Renaissance Technologies

- No public technical work. Mercer interviews (~2014, Numberphile) and Simons biography (Zuckerman, *The Man Who Solved the Market*, 2019) hint at: HMM-based regime detection, very large feature libraries (10,000+ signals), aggressive deflation/orthogonalization. **No methodology you can cite.**

### 5C. Citadel

- Limited public ML work. Some published research on optimal execution and crossing networks (Citadel Connect). Most signal is recruiting (PhD-from-physics pipeline).

### 5D. Jane Street

- **Public**: blog.janestreet.com — "Real-world machine learning" series (2019+); Signals & Threads podcast; *Deep learning experiments in OCaml* (2019). Themes: invest heavily in data quality (2.3 TB/day market data); domain expertise > model complexity; ETF-vs-basket Gaussian assumption breaks under arbitrage bounds — *use bounded distributions when bounds are known*.
- **Jane Street 2020 Kaggle Market Prediction**: top solutions used MLP+autoencoder denoise (best public: 1st place "Yirun Zhang" used MLP+AE+date weighting; 5th-tier ensemble approach).
- **Jane Street 2024 Kaggle Real-Time Market Data Forecasting**: top solutions (e.g., Volkova GitHub `evgeniavolkova/kagglejanestreet`) — used neural networks with online updating, custom feature engineering and time-aware CV. The competition revealed: feature-target correlations are non-stationary; avoiding overfit dominates.

### 5E. Hudson River Trading

- Limited public work. Some open-source contributions to kernel networking. No directly applicable ML methodology.

### 5F. WorldQuant 101 Formulaic Alphas

- Kakushadze (arXiv 1601.00991, 2016). 101 formulaic alphas with 0.6–6.4 day holding periods. **Directly relevant** to your 5-day horizon. Most are functions of returns/volume with rank/time-decay/correlation operators. Implement via DolphinDB (15× faster than pandas median, 100× for 27% of alphas) or `gplearn`-compatible operators in Polars. For microcaps, alphas #4, #6, #12, #38, #42, #46, #54, #98, #101 are most plausibly relevant. Complexity 2/5; lift uncertain (many of these are correlated with what you have, but the long tail of #80–#101 contains exotic operators you may not have considered).

### 5G. Quantopian / Quantconnect public research

- Quantopian's Pipeline / Alphalens framework taught a generation of quants the rank-based factor evaluation paradigm. The `alphalens` library is still the reference.

### 5H. Numerai community techniques

- **Feature neutralization** — Project predictions onto residual space orthogonal to known features. Standard Numerai pseudo-code: `sub -= F·(F⁺·sub); sub /= sub.std()`. **Highest-value Numerai transfer to MoMTrans**: neutralize predictions with respect to (a) market-cap, (b) sector dummies, (c) prior-day return, (d) realized vol. This will *reduce* your Spearman by 0.02–0.05 but *deflate it honestly* — what's left is genuinely novel signal that survives multiple-testing. Complexity 1/5; **mandatory honest practice**.
- **Era boosting** — train tree on residuals from worst-performing eras; analog for you: train on residuals from worst-Sharpe weeks/quarters.
- **Per-era CV** — never mix samples from the same era across train/test. Direct analog of your walk-forward.
- **MMC (Meta-Model Contribution)** — measures how predictions improve the *consensus* model. For your portfolio context, MMC ≈ how your model differs from a sector-momentum baseline.

### 5I. Kaggle quant winners

- **Optiver Realized Volatility 2021** — winning solutions: ensemble of LightGBM + 1D-CNN/MLP on engineered features (log-returns at multiple windows, realized volatility decomposed, Garman-Klass, Parkinson, BV, RV signed, order book imbalance). Top features: *realized volatility weighted by linear time-decay*, *log-returns over the second half of the window* (informational asymmetry within bucket). Lesson: feature engineering >> architecture.
- **Optiver Trading at the Close 2023** — 1st place (`hyd`): GBDT (LightGBM) with hundreds of engineered features on imbalance/wap diffs/lags + zero-sum normalization (`goto_conversion`). Critical insight: *target is closing-auction return*, has very narrow signal and dominated by feature lag/diff structure; XGBoost/LightGBM beat NNs in most submissions. **For your 5-day horizon** the same likely holds — make sure you have a strong GBDT baseline you cannot beat with the transformer.
- **JPX Tokyo Stock Exchange Prediction 2022** — top solutions used Linear-Tree hybrids, noise-reduced targets (Winsorize, target neutralization), and ranking method (Spearman rank correlation as objective via `lightgbm-ranker`). 62nd-place solution by Takeya Masahiro publicly documents Linear-Tree + ranking objective. **Direct transfer**: switch your loss from MSE/BCE to a *listwise rank loss* (NeuralNDCG / ApproxNDCG / softrank).
- **G-Research Crypto Forecasting 2021** — winners used long-window features + LightGBM + careful per-asset CV.

---

## 6. Anonymous Submissions / Workshops / Frontier (2025–2026)

### 6A. NeurIPS 2025 workshops

- **"Recent Advances on Time Series Foundation Models: Have We Reached the BERT Moment?" (BERT²S)** — San Diego, Dec 7, 2025. Position: *TSFMs still need full fine-tune to beat lightweight supervised baselines*; some tabular FMs rival TSFMs without being TS-specific. Direct relevance: your TabTransformer-with-SSL is *exactly the lightweight supervised baseline* the workshop concedes is competitive. Don't be lured into a 2B-parameter TSFM without an A/B.
- **NeurIPS 2024 Time Series Workshop**: TabPFN-TS (Hoo et al. arXiv 2501.02945, *From Tables to Time*) — TabPFN-v2 + simple feature engineering matches Chronos-Large with 11M params vs. 65×. **Strong evidence** that TabPFN-v2 in-context learning is sufficient for your scale.
- **ICML 2025 Foundation Models for Structured Data** workshop — joint tabular/time-series; ICLR 2026 follow-up planned.

### 6B. ICLR 2026 anonymous submissions

OpenReview lists active submissions on: tabular foundation models (TabICLv2 arXiv 2602.11139, scalable open tabular FM), distribution-shift robustness, agent-based time series. Search OpenReview "tabular" and "time series" with status=Active for 2026 cycle.

### 6C. ICML 2025 / 2026

- **TabPFN-TS** (Hoo et al. ICML 2025 workshop track) — already cited.
- **TabPFNv2** (Hollmann et al. ICML 2025 / arXiv 2501) — the v2 release.
- **TabICLv2** (arXiv 2602.11139) — better/faster scalable tabular FM with attention-fading mitigation.

### 6D. AAAI 2025/2026 quantitative finance

- AAAI 2025 had a Bridge program on AI-for-Finance. Key papers: bagging-expert depolarized MMoE (arXiv on AAAI 2025); FinRobot agent platforms (Zhou et al. ICAIF 2024).

---

## 7. Theoretical Frontiers (No Empirical Track Record Yet)

### 7A. Generative models for return distributions

- **Conditional flow matching / rectified flow** — Liu, Gong, Liu (ICLR 2023) "Flow Straight and Fast"; Wang et al. (ICLR 2025) "Rectified Diffusion: Straightness Is Not Your Need". For finance: Sundial (Liu et al. 2025) introduces flow-matching for probabilistic time series. **Very speculative** for microcap intraday — no published direct application. Complexity 5/5; high research-novelty value, low immediate ROI.
- **Score-based diffusion for asset prices** — diffusion models for financial trajectories are mostly synthetic-data tools (e.g., for backtest augmentation), not predictive models. Use case for you: generate synthetic ELITE-class samples to address the 85-positive starvation. Caveat: generated samples may not preserve causal structure — *use only if you treat the generator as a regularizer*, not as new data.
- **Schrödinger bridges for financial trajectories** — De Bortoli et al. (NeurIPS 2021) DSB; financial application by Hua et al. (arXiv 2024) for option pricing. **Speculative; not recommended.**

### 7B. Causal inference in finance

- **DoWhy** (Microsoft Research, Sharma & Kiciman 2020) and **EconML** (Microsoft Alice project, 2019+) for treatment-effect estimation. For MoMTrans: treat "news-catalyst-type" as treatment, "5-day return" as outcome, all microstructure as confounders. Estimate the *causal effect* of catalyst type via Double-ML (Chernozhukov et al. *Econometrics J* 2018). Output is a more honest catalyst feature.
- **NOTEARS** — Zheng et al. (NeurIPS 2018) — formulates DAG learning as continuous optimization. **Reality check**: Kaiser & Sipos (arXiv 2104.05441) "Unsuitability of NOTEARS for Causal Graph Discovery" demonstrate scale-invariance failures. **Use with caution; do not rely on NOTEARS as a black-box causal-discovery oracle for finance.** PC and FCI are safer.
- **Invariant Causal Prediction** — Peters, Bühlmann, Meinshausen (*JRSS-B* 2016). For multi-environment data (different market regimes), find features whose conditional distribution given target is invariant. **Strong fit conceptually**: your 16 walk-forward folds = 16 environments. Run ICP on the engineered feature set to identify *invariant predictors*; these should generalize. Complexity 4/5; expected lift on out-of-time Sharpe 0.1–0.3.
- **López de Prado on causality**: 2024+ ResearchGate paper "Virtually all journal articles in factor investing make associational, not causal, claims" — argues factor research without causal graph is statistically suspect. Aligns with the message above.

### 7C. Topological / geometric ML

- **Persistent homology for financial time series** — Gidea & Katz (*Physica A* 2018) detected dot-com and 2008 crashes via persistence landscape p-norms; extensions in Rudkin, Qiu & Dłotko (arXiv 2110.00098, 2021) on uncertainty/volatility persistence norms; *Systems* 2025 "Change Point Detection in Financial Market Using TDA" applies it to multi-time-series CPD; *Neural Computing and Applications* 2024 paper specifically tests TDA features for forecasting.
- **For MoMTrans**: compute persistence diagram of the d0 morning minute-bar trajectory (Takens embedding, dim=3, lag=1), summarize via L¹/L²-norm of persistence landscape. Yields 4–8 features that capture *topological complexity* of the morning's price path. Your prior result that "sequence branch over minute bars was harmful" is consistent with raw sequence being too noisy; persistence-norm features may be the *right* level of abstraction. **Genuinely novel for microcap; complexity 4/5; speculative lift 0.005–0.02 but a defensible original-research bet.**

### 7D. Symbolic regression / equation discovery

- **gplearn** (Stephens 2015+) — Python GP for SR. **alphagen** (RL-MLDM 2023, GitHub, NeurIPS 2023) generates formulaic alpha factors via reinforcement learning, beats gplearn baselines. **Warm-start GP** (Ren, Qin, Li arXiv 2412.00896, 2024) — warm-started GP for alpha mining on Chinese 2020-2024 data with superior out-of-sample IC.
- **AI Feynman** (Udrescu & Tegmark 2020) — symbolic discovery for physics; less direct fit.
- **DSO (Deep Symbolic Optimization)** — Petersen et al. ICLR 2021. Better than gplearn on benchmarks per *NeurIPS* 2021 SR benchmark.
- **For MoMTrans**: run alphagen for 24 hours on your 54 features as primitives + the new microstructure features; output is 50–200 formulaic alphas. Filter by IC and feature-neutralized IC; keep top 10–20. **Complexity 3/5; expected lift 0.005–0.025; very low compute risk on RTX 5070.**

---

## 8. Specific Gaps from Prior Report — New Lens

### 8A. Beyond pseudo-labeling on the 60k unlabeled

- **Domain Adversarial Neural Networks (DANN)** — Ganin et al. (*JMLR* 2016). Treat labeled (20k) vs. unlabeled (60k) as source/target domain; gradient reversal layer makes representations domain-invariant. *Caveat*: only useful if labeled and unlabeled have a meaningful distribution shift (which they do — labeled candidates were filtered).
- **Energy-based models for tabular SSL** — Pang, Nijkamp, Cui, Han, Wu (NeurIPS 2020) "Semi-supervised Learning by Latent Space Energy-Based Model of Symbol-Vector Coupling"; JEM (Grathwohl et al. ICLR 2020). EBM gives you a joint p(x,y) and naturally handles SSL. Strong theoretical fit; complexity 5/5; experimental.
- **Open-set / OOD detection in semi-supervised** — for the unlabeled 60k, some are *fundamentally different* (illiquid, halted, etc.); flag and exclude rather than pseudo-label. Use Mahalanobis distance in embedding space (Lee et al. NeurIPS 2018) or Energy score (Liu et al. NeurIPS 2020).

### 8B. Multi-task learning beyond 4 thresholds

Auxiliary tasks that should *help* your main task:
- Predict next-day volume (LightGBM-easy, regularizes attention to volume features).
- Predict bid-ask spread at d+1 open.
- Predict news arrival probability in next 3 days.
- Predict 1-day, 3-day, 10-day return jointly with 5-day (multi-horizon).
- Predict whether the candidate will be halted (microcap halt risk is huge and information-rich).
- Predict realized volatility 1-day forward (Optiver 2021 winners' insight: vol prediction is *easier* than direction and provides regularization).

PLE > MMoE > shared-bottom for combining these. Complexity 3/5; expected lift 0.01–0.03 on main task.

### 8C. Conformal coverage on autocorrelated finance data

This is a real problem. Standard split-conformal **does not** maintain marginal coverage under temporal dependence. Options:
- **Adaptive Conformal Inference (ACI)** — Gibbs & Candès (NeurIPS 2021) — online update of α target.
- **HopCPT** (Auer et al. ICML 2023) — Hopfield-network retrieval finds similar regimes.
- **EnbPI** (Xu & Xie ICML 2021) — ensemble batch prediction intervals.
- **MSCP / AcMCP** (Wang & Hyndman Monash WP 2024) — multi-step conformal with autocorrelation-aware controllers.
- **Temporal Conformal Prediction (TCP-RM)** (arXiv 2507.05470, 2025) — Robbins-Monro online calibration explicitly for finance.
- **Practical recommendation**: HopCPT or ACI with embargo, calibrate per-tier, target 90% coverage on 5-day return prediction intervals. Honest report: empirical coverage will likely be 78–85% in tail regimes — **disclose this**.

### 8D. Gradient flow / loss landscape for small-data tabular

- **Sharpness-Aware Minimization (SAM)** — Foret et al. ICLR 2021 (already in prior report). For small data, ASAM (Kwon et al. ICML 2021) and GSAM (Zhuang et al. ICLR 2022) are more sophisticated.
- **Hessian eigenspectrum monitoring** — `pyhessian` library; for 500K-param models, compute top-10 eigenvalues at end of each fold; rapid drop in λ_max-to-λ_min ratio indicates good landscape.
- **Linear Mode Connectivity** — Frankle, Dziugaite, Roy (ICML 2020); ensembles of LMC-connected models often outperform random ensembles for small data.

---

## 9. Engineering / Systems Angle

### 9A. Polars vs. pandas vs. DuckDB for feature engineering

For your scale (4 TB tick data, ~6M trades/day microcaps universe):
- **Pandas** — disqualified for the tick layer; will OOM.
- **Polars** — Rust columnar, multi-threaded, lazy planner with projection/predicate pushdown and streaming for >RAM. Benchmarks consistently show 5–10× faster than pandas, ~30–60% lower memory; CSV reading 7.7× faster, joins 5× faster, groupby comparable to DuckDB. **Recommended primary engine for your tick→feature pipeline.**
- **DuckDB** — embedded OLAP SQL engine, queries Parquet directly without ETL, automatic spill-to-disk. Best when truth-of-data is in Parquet/Iceberg lake and you want SQL composability. **Use DuckDB to read Polygon flatfiles, Polars to compute features, then materialize features to Parquet.** This is the modern standard 2024–2026 pattern.

### 9B. Triton kernels / custom CUDA on RTX 5070

- For 500K-parameter transformers, kernel-level optimization is **probably not worth it** (GPU is underutilized at this scale). Save Triton effort for: (a) *VPIN bucket aggregation* (custom kernel; ~2× speedup), (b) *Hawkes likelihood* (large win), (c) *NeuralNDCG sort* (already torchsort).
- sm_120 (Blackwell) on RTX 5070: ensure CUDA 12.8+, PyTorch 2.6+, FlashAttention 2.6+ with sm_120 kernels. Some kernels not yet ported; check `torch.compile` fallback.

### 9C. ONNX Runtime / TensorRT for inference latency

- **For 5-day-horizon trades** latency is non-critical; inference is offline morning batch. Skip ONNX/TensorRT for production.
- **If extending to intraday/minute-horizon strategies**, export TabTransformer to ONNX, run with TensorRT 10+ for 3–5× speedup.

### 9D. Model serving patterns for low-latency intraday

- For batch morning inference: PyTorch + Polars + parquet output to broker API. <30 sec end-to-end on RTX 5070.
- For intraday: use a Triton Inference Server, batch inference per-minute, GPU residency.

---

## 10. Market Impact / Capacity Modeling — **Existential at $5M AUM Microcap**

**This is the section that determines whether MoMTrans is a backtest curiosity or a real strategy.** Microcaps have median daily $-volume of $1–10M; a $50K position taking 5–10% of ADV will move price 0.5–3%. Your alpha is 5–10% over 5 days — *most of which can be eaten by impact*.

### 10A. Almgren-Chriss optimal execution

Almgren & Chriss (*Journal of Risk* 2000). Mean-variance frontier of execution: minimize expected cost (permanent + temporary impact) + λ × variance. Closed-form optimal trajectory for liquidating Q over T. Standard parameters: η (temporary impact slope) and γ (permanent impact slope); both estimable from your `trades_v1` data per name. **For MoMTrans entry**: you are *acquiring* over the 9:30–10:00 window; A-C says VWAP-like execution with front-loading proportional to risk aversion. Complexity 3/5.

### 10B. Square-root law of impact

Bouchaud and collaborators (CFM): Tóth, Eisler, Bouchaud (*PRX* 2011); Bucci, Mastromatteo, Benzaquen, Bouchaud (arXiv 1905.04569, 2019) "Impact is not just volatility"; Maitrier, Loeper, Kanazawa & Bouchaud (arXiv 2502.16246, Aug 2025) **"The 'double' square-root law: Evidence for the mechanical origin of market impact using Tokyo Stock Exchange data"**. Sano et al. (arXiv 2411.13965, 2024) "Does the square-root price impact law belong to the strict universal scalings?" — complete TSE survey supporting universality. **Practical formula**: Δp/σ ≈ Y · sign(Q) · √(|Q|/V_daily), Y ≈ 0.5–1 (Y-coefficient is asset-class- and regime-specific; for microcaps likely 1–2× large-cap value). **Use this as a hard capacity constraint**: for a $5M AUM strategy, position sizes should target Q/V_daily < 0.05 to keep impact <0.5%. Complexity 1/5; **mandatory**.

### 10C. Adaptive arrival-price algorithms

Modern optimal execution literature: Cartea-Jaimungal-Penalva *Algorithmic and High-Frequency Trading* (Cambridge 2015); Guéant *The Financial Mathematics of Market Liquidity* (CRC 2016).

### 10D. Recent ML approaches to optimal execution

- Deep RL extensions to A-C: arXiv 1403.2229 (foundational); modern work in Cartea group at Oxford.
- For your scale: **A-C with empirically-estimated Bouchaud square-root impact is the right level**; deep RL execution is overkill for $5M AUM and 30-min execution windows.

### 10E. Capacity at $5M AUM microcap

Honest math:
- 30 candidates/day × $50K avg position = $1.5M turnover/day.
- Across 30 candidates with median daily volume $5M, your participation = $50K/$5M = 1% per name = ~0.1% expected price impact (Bouchaud Y=1) — manageable.
- BUT on ELITE/HIGH tier names (often less liquid) participation can hit 5–10%, impact 0.5–1.5% — this materially eats expected return.
- **Capacity ceiling realistic**: ~$8–15M AUM before impact starts dominating expected return on ELITE; BROAD/VETOED tiers can scale to $30–50M.
- **Action item**: build a per-name capacity model (Q*=argmax E[return - impact] given your edge × position) and use it as a *hard cap* in Kelly sizing.

---

## Final Synthesis

### The 5–7 Highest-Leverage NEW Research Bets (Not Covered in Prior Report)

1. **Microstructure feature pack from `trades_v1`**: VPIN (Lee-Ready-based, not BVC), Cont-Kukanov-Stoikov OFI computed from trade-side classification, Kyle's λ, dark-pool TRF print magnitudes, Hawkes self-excitation summary statistics. *Expected lift on Spearman: 0.03–0.07. This is the largest single move and dominates everything else.*

2. **Benzinga FinBERT/FinGPT catalyst embeddings + LLM-graded catalyst-type taxonomy**, used both as features and as MTL auxiliary targets. *Expected lift on Spearman 0.01–0.03; especially valuable on ELITE where idiosyncratic catalyst quality dominates.*

3. **Replace post-hoc Kelly with IQN+CVaR distributional-RL sizing** (or at minimum, conservative-offline-RL with quantile critic). Honest fat-tail handling will materially improve realized Sharpe even if Spearman is unchanged. *Expected Sharpe lift 0.3–0.6.*

4. **CPCV + Deflated Sharpe + PBO replacing 16-fold walk-forward**, with feature neutralization (Numerai-style) against sector/beta/market-cap/realized-vol. *This may reduce your reported Spearman from 0.16 to 0.10–0.12 but is honest, and almost certainly invalidates ELITE as currently scored — necessary surgery.*

5. **PLE multi-task across the 4 tiers with auxiliary tasks** (next-day volume, spread, halt probability, multi-horizon returns). *Expected lift on the data-starved ELITE specifically 0.01–0.03; mitigates the negative-transfer problem your shared encoder may currently suffer.*

6. **Bouchaud square-root impact as a hard capacity constraint integrated into Kelly**. *No Spearman lift but prevents alpha decay at scale; mandatory for $5M+ AUM.*

7. **Sympathy-stock peer features** (k-NN by 60-day correlation + sector/cap match; aggregate peer features) — cheap GNN-without-GNN. *Expected lift 0.01–0.03; complexity 2/5.*

### Cross-Domain Transfers Most Likely to Break the Spearman 0.16 Ceiling

- **Optiver 2021/2023 Kaggle winners' feature engineering playbook**: realized vol decomposition (Garman-Klass, Parkinson, BV, RV-signed), linear-time-decay-weighted vol, log-returns over half-windows. This is ~30 features that work on any equity intraday horizon.
- **Numerai feature neutralization** — orthogonalize against sector/beta/cap exposures. Direct, free Spearman validation.
- **JPX 2022 Linear-Tree + listwise rank loss** — switch from BCE to NeuralNDCG/ApproxNDCG (already in prior report) AND add a Linear-Tree (e.g., LinearTree with LightGBM leaves) ensemble member.
- **Sports-betting calibration > accuracy lesson** + UPenn fractional Kelly evidence — your ELITE posterior is almost certainly miscalibrated; isotonic recalibration alone may rescue 0.01 Sharpe.
- **Medical rare-disease meta-learning (DFML / MAML)** — directly addresses ELITE 85-positive starvation.
- **Microstructure invariance (Kyle-Obizhaeva 2016)** + Bouchaud "double" square-root law (2025) — gives a *theoretical* anchor for cross-sectional normalization that purely empirical methods miss.

### Genuinely Unexplored Research Questions Where You Could Do Original Work

- **No published peer-reviewed paper targets microcap intraday gap-up continuation with deep tabular models.** You are already at the frontier. Publishable angles:
  1. *VPIN/OFI feature impact on microcap gap continuation* — direct empirical paper, EJF/JFM-tier.
  2. *Persistence-homology features of morning-bar trajectories as gap-fade predictors* — niche but novel; *Quantitative Finance* could publish.
  3. *Distributional-RL Kelly under microstructure-invariance constraints* — methodological paper bridging Cartea-Jaimungal RL execution and IQN.
  4. *Feature-neutralized Spearman vs. sector/beta — honest microcap alpha* — calls out the sector-tilt confounding most microcap papers ignore.
  5. *Empirical capacity ceiling for microcap intraday strategies under Bouchaud impact* — high industry interest.
- **Folklore vs. validated**: 
  - VPIN/OFI/Lee-Ready: validated.
  - BVC: empirically inferior to LR/TR in equities (don't use BVC unless you must).
  - Numerai feature neutralization: validated by community over years.
  - "Renaissance uses HMMs / 10,000 features": folklore, no public confirmation.
  - RL trading dominates supervised: folklore — published evidence is mixed; supervised + good sizing usually wins on realistic data.
  - LLM for stock prediction: validated for sentiment lift; speculative for direct alpha generation.
  - TDA for finance: validated as crash predictor; speculative as continuous alpha source.
  - NOTEARS as causal-discovery oracle: **not validated**; known scale-invariance issues.

### What's Mythical and Should Be De-emphasized

- "Bigger transformer → better tabular" — empirically false at your scale, your prior result confirms; aligns with TabPFN-TS/Chronos benchmarks.
- "Distillation gives free Spearman" — usually trades capacity for stability; net Spearman often unchanged.
- "Mamba/KAN beats transformers on tabular" — 2024 benchmark (TabM, TabPFN-v2) does not show clear KAN/Mamba wins on tabular finance; speculative.
- "More unlabeled data = better SSL" — true up to a *distribution-similarity* limit; your 60k unlabeled may include too much OOD junk; quality > quantity.

---

## Caveats

- **Speculative vs. proven**: Sections marked "complexity 5/5" or "speculative" (Schrödinger bridges, neural-symbolic alpha, persistence homology, EBM-tabular SSL, NOTEARS for finance, full LLM-RL stacks like FinRL-DeepSeek) are research bets, not engineering. They have publishable upside but should not crowd out the proven moves in §1A and §3.
- **The "leaked quant fund" content is mostly folklore.** Renaissance, Citadel, HRT publish very little; what circulates online is reconstructed inference, often wrong in detail. Two Sigma's Halite/Kaggle and Jane Street's blog/podcast/Kaggle 2020+2024 are the only firm-attributable sources. WorldQuant's 101 alphas paper is the closest we have to a "fund factor library reveal" and it is from 2015 — newer and more profitable alphas are not public.
- **Predicted Spearman lifts above are honest order-of-magnitude estimates**, not promises. The single largest variance source is whether your microstructure features replicate sector/beta exposures you already have implicitly — feature-neutralization will tell you.
- **ELITE is a data problem, not a modeling problem.** With 85 positives per fold, no architectural choice will rescue Spearman 0.004; you need either (a) more positives via lower threshold + ordinal CORN/CORAL (already in prior report — re-emphasize), (b) auxiliary tasks providing inductive transfer (PLE), (c) honest acceptance via DSR that ELITE is not statistically significant and should not receive Aggressive Kelly capital.
- **At $5M AUM microcap, capacity and execution dominate alpha.** A Spearman 0.20 strategy that loses 1.5% to impact has lower realized Sharpe than a Spearman 0.12 strategy that loses 0.3%. The Bouchaud square-root law is *the most under-emphasized item in the prior report*.
- **Do CPCV + DSR + PBO before any further architecture work.** If your 0.16 Spearman fails DSR, you've spent compute optimizing a phantom; if it passes, you have evidence of real edge worth scaling features against. Order of operations matters.
- **Date sensitivity**: this report was assembled in May 2026; the 2026 papers cited (Wang & Hyndman 2026, TabICLv2 arXiv 2602.11139, Hu et al. arXiv 2603.09734, Maitrier et al. revised Aug 2025, etc.) are at the absolute frontier and have limited replication evidence. Treat the 2025–2026 work as "promising" rather than "proven", and the 2018–2023 foundational work as "battle-tested".