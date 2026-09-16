"""MoMTrans v5 — frontier-research components.

Per docs/research-log/131_momtrans_v5_enhancement_roadmap.md (Tier S items):

  S1. CORN ordinal head (Shi et al., arXiv:2111.08851, 2021)
       — replaces 4 independent BCE specialists with 1 chained head
       — guarantees rank consistency P(R≥40%) ≤ ... ≤ P(R≥10%)
       — borrows ELITE statistical strength from BROAD's 4,163 positives

  S2. Spearman soft-rank loss (Blondel et al., arXiv:2002.08871)
       — sigmoid pairwise-comparison rank approximation (no torchsort dep)
       — per-day grouping aligned with the strategy's top-K daily picks
       — directly optimizes the metric we measure

  S3. Muon optimizer + EMA-of-weights (Yandex 2026 benchmark)
       — Muon for 2D matrix params; AdamW for 1D bias/LN
       — EMA decay=0.999 for distribution-shift robustness

  S4. Mixup augmentation (Zhang et al. 2018)
       — feature-space interpolation with logit-space label interpolation
       — calibrated for the imbalanced ELITE-tail problem (~85 positives/fold)

This module is IMPORTABLE only — see ml_v5_train_corn.py for the trainer.
"""
from __future__ import annotations
import math
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


# ── S1. CORN ordinal head ────────────────────────────────────────────


class CORNHead(nn.Module):
    """Conditional ORdinal Regression Network head.

    For K nested ordinal thresholds (e.g., R≥10/15/25/40), CORN outputs
    K-1 conditional probabilities:
      p_1 = P(R≥t_1)
      p_2 = P(R≥t_2 | R≥t_1)
      p_3 = P(R≥t_3 | R≥t_2)
      ...

    The unconditional probabilities at each level are obtained as cumulative
    products: P(R≥t_k) = prod(p_1, ..., p_k). This guarantees rank
    consistency P(R≥40%) ≤ P(R≥25%) ≤ P(R≥15%) ≤ P(R≥10%).

    Loss: BCE on each conditional probability, masked so BCE_k only fires
    on samples that reached level (k-1). This is what makes CORN
    statistically efficient — every BROAD positive contributes to learning
    the ELITE conditional via the lower-level conditionals.
    """
    def __init__(self, in_features: int, n_thresholds: int):
        """n_thresholds = K-1 (number of conditional probabilities to predict)."""
        super().__init__()
        self.n_thresholds = n_thresholds
        self.proj = nn.Linear(in_features, n_thresholds)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Returns conditional logits, shape (B, n_thresholds)."""
        return self.proj(x)

    def cumulative_probs(self, conditional_logits: torch.Tensor) -> torch.Tensor:
        """Convert conditional logits to UNCONDITIONAL probabilities at each level.
        P(R≥t_k) = sigmoid(z_1) * sigmoid(z_2) * ... * sigmoid(z_k)
        Returns shape (B, n_thresholds)."""
        cond_probs = torch.sigmoid(conditional_logits)
        return torch.cumprod(cond_probs, dim=1)


def corn_loss(conditional_logits: torch.Tensor, y_ordinal: torch.Tensor,
                n_thresholds: int) -> torch.Tensor:
    """CORN loss: chained BCE with conditional masking.

    y_ordinal: (B,) integer in [0, n_thresholds] — number of ordinal
               levels the sample reaches. e.g., y=0 means below all
               thresholds (SKIP); y=n_thresholds means above all (ELITE).

    For sample with y_ordinal=k:
      BCE(p_1, 1)         — must predict P(reach 1) = 1 → contributes
      BCE(p_2, 1)         — must predict P(reach 2 | reach 1) = 1 → contributes
      ...
      BCE(p_k, 1)         — must predict P(reach k | reach k-1) = 1 → contributes
      BCE(p_{k+1}, 0)     — must predict P(reach k+1 | reach k) = 0 → contributes
                              (only if k < n_thresholds)
      BCE(p_j, _)         — for j > k+1, sample didn't reach level j → MASKED OUT

    Implementation: mask each BCE term by whether the sample is "in scope"
    for that conditional level (reached at least level j-1).
    """
    B = y_ordinal.size(0)
    K = n_thresholds
    # In-scope mask: (B, K) — sample is in scope for conditional j if y >= j-1
    # i.e., it reached at least level j-1, so we have a labeled outcome for j.
    levels = torch.arange(K, device=y_ordinal.device).unsqueeze(0)  # (1, K)
    in_scope = (y_ordinal.unsqueeze(1) >= levels).float()  # (B, K)
    # Target: 1 if sample reached level j (i.e., y >= j+1 because j is 0-indexed)
    target = (y_ordinal.unsqueeze(1) >= levels + 1).float()  # (B, K)
    # BCE per (B, K), mask out, then average
    bce = F.binary_cross_entropy_with_logits(
        conditional_logits, target, reduction="none",
    )
    masked = bce * in_scope
    n_in_scope = in_scope.sum().clamp(min=1.0)
    return masked.sum() / n_in_scope


# ── S2. Spearman soft-rank loss ─────────────────────────────────────


def soft_rank(x: torch.Tensor, tau: float = 0.1) -> torch.Tensor:
    """Differentiable rank approximation via sigmoid pairwise comparisons.

    rank(x_i) ≈ sum_j sigmoid((x_i - x_j) / tau) + 0.5
    (the +0.5 is the i==j term: sigmoid(0)=0.5)

    O(n²) — fine for our per-day batch sizes (~10-30 candidates).
    Smaller tau = sharper (closer to true rank) but harder gradient.
    tau=0.1 is a typical sweet spot.
    """
    diff = x.unsqueeze(0) - x.unsqueeze(1)  # (n, n) where [i, j] = x_j - x_i
    return torch.sigmoid(diff / tau).sum(dim=0)  # (n,) — rank of each x_i


def spearman_correlation(pred: torch.Tensor, target: torch.Tensor,
                          tau: float = 0.1) -> torch.Tensor:
    """Differentiable Spearman ρ via soft-rank.

    Returns scalar in [-1, 1] (we maximize this; loss is the negation)."""
    n = pred.size(0)
    if n < 2:
        return torch.tensor(0.0, device=pred.device)
    pred_rank = soft_rank(pred, tau=tau)
    target_rank = soft_rank(target, tau=tau)
    pred_centered = pred_rank - pred_rank.mean()
    target_centered = target_rank - target_rank.mean()
    cov = (pred_centered * target_centered).sum()
    var_p = (pred_centered ** 2).sum().clamp(min=1e-8)
    var_t = (target_centered ** 2).sum().clamp(min=1e-8)
    return cov / torch.sqrt(var_p * var_t)


def per_day_spearman_loss(pred: torch.Tensor, target: torch.Tensor,
                            day_ids: torch.Tensor, tau: float = 0.1,
                            min_per_day: int = 3) -> torch.Tensor:
    """Per-day-grouped Spearman loss. Groups predictions by day_id, computes
    Spearman ρ within each day, returns -mean(ρ_day) as the loss.

    Days with fewer than `min_per_day` candidates are skipped (Spearman is
    unstable on tiny groups).
    """
    unique_days = day_ids.unique()
    losses = []
    for d in unique_days:
        mask = day_ids == d
        if mask.sum() < min_per_day:
            continue
        rho = spearman_correlation(pred[mask], target[mask], tau=tau)
        losses.append(-rho)  # negate so minimizing loss maximizes correlation
    if not losses:
        return torch.tensor(0.0, device=pred.device, requires_grad=True)
    return torch.stack(losses).mean()


# ── S3. Muon optimizer + EMA weights ─────────────────────────────────


def newton_schulz_orthogonalize(G: torch.Tensor, n_steps: int = 5,
                                  eps: float = 1e-7) -> torch.Tensor:
    """Newton-Schulz iteration to compute (G G^T)^(-1/2) G ≈ orth(G).

    Used by Muon for orthogonalizing weight updates. Per Keller Jordan's
    blogpost (2024). Converges in 3-5 iterations typically.
    """
    # Normalize to unit Frobenius norm for numerical stability
    G_norm = G.norm() + eps
    X = G / G_norm
    # Coefficients tuned by Keller Jordan for fast convergence
    a, b, c = 3.4445, -4.7750, 2.0315
    for _ in range(n_steps):
        A = X @ X.T
        B = b * A + c * (A @ A)
        X = a * X + B @ X
    return X * G_norm


class Muon(torch.optim.Optimizer):
    """Muon: Newton-Schulz orthogonalized momentum optimizer for 2D params.

    Per Keller Jordan 2024 (kellerjordan.github.io/posts/muon). Yandex 2026
    benchmark (arXiv:2604.15297) shows consistent gains over AdamW on
    tabular MLPs.

    USE FOR 2D MATRICES ONLY. 1D params (biases, LayerNorm) should use
    AdamW. The MoMTrans wrapper handles this split.
    """
    def __init__(self, params, lr: float = 0.02, momentum: float = 0.95,
                  ns_steps: int = 5, weight_decay: float = 0.0):
        defaults = dict(lr=lr, momentum=momentum, ns_steps=ns_steps,
                          weight_decay=weight_decay)
        super().__init__(params, defaults)

    @torch.no_grad()
    def step(self, closure=None):
        loss = closure() if closure is not None else None
        for group in self.param_groups:
            lr = group["lr"]
            mom = group["momentum"]
            ns_steps = group["ns_steps"]
            wd = group["weight_decay"]
            for p in group["params"]:
                if p.grad is None:
                    continue
                if p.dim() != 2:
                    # Muon is matrix-only; this should not happen if you
                    # split params correctly upstream
                    continue
                state = self.state[p]
                if "buf" not in state:
                    state["buf"] = torch.zeros_like(p)
                buf = state["buf"]
                buf.mul_(mom).add_(p.grad)
                update = newton_schulz_orthogonalize(buf, n_steps=ns_steps)
                # Scale update by sqrt of param dimensions (Muon's standard scaling)
                scale = math.sqrt(p.size(0) / p.size(1))
                if wd > 0:
                    p.mul_(1.0 - lr * wd)
                p.add_(update, alpha=-lr * scale)
        return loss


def split_params_for_muon(model: nn.Module):
    """Split model parameters into (matrix_params, scalar_params) for
    Muon-on-matrices + AdamW-on-rest hybrid optimization."""
    matrix_params = []
    scalar_params = []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        if p.dim() == 2 and p.size(0) >= 8 and p.size(1) >= 8:
            # 2D params with both dims >=8 use Muon (skip tiny matrices like
            # 1×N projections which are effectively 1D)
            matrix_params.append(p)
        else:
            scalar_params.append(p)
    return matrix_params, scalar_params


class EMAWeights:
    """Exponential moving average of model parameters for distribution-shift
    robustness. Per Yandex 2026 tabular benchmark, AdamW+EMA is a simple
    consistent improvement.

    Usage:
      ema = EMAWeights(model, decay=0.999)
      # ... training step ...
      ema.update(model)
      # ... at inference ...
      ema.apply_to(model)   # swap EMA weights into model
      pred = model(x)
      ema.restore(model)    # restore training weights
    """
    def __init__(self, model: nn.Module, decay: float = 0.999):
        self.decay = decay
        self.shadow = {n: p.detach().clone() for n, p in model.named_parameters()
                          if p.requires_grad}
        self._stash = {}

    @torch.no_grad()
    def update(self, model: nn.Module) -> None:
        for n, p in model.named_parameters():
            if not p.requires_grad: continue
            self.shadow[n].mul_(self.decay).add_(p.detach(), alpha=1.0 - self.decay)

    @torch.no_grad()
    def apply_to(self, model: nn.Module) -> None:
        self._stash = {n: p.detach().clone() for n, p in model.named_parameters()
                          if p.requires_grad}
        for n, p in model.named_parameters():
            if n in self.shadow:
                p.copy_(self.shadow[n])

    @torch.no_grad()
    def restore(self, model: nn.Module) -> None:
        for n, p in model.named_parameters():
            if n in self._stash:
                p.copy_(self._stash[n])
        self._stash = {}


# ── S4. Mixup augmentation ───────────────────────────────────────────


def mixup_features_and_ordinal_labels(
    x: torch.Tensor, y_ordinal: torch.Tensor, alpha: float = 0.4,
):
    """Mixup for tabular features + ordinal labels.

    Features: standard linear interpolation in feature space.
    Ordinal labels: convert to soft cumulative-probability vector
                   (B, K_thresholds), then linearly interpolate.

    Returns (x_mixed, y_soft_a, y_soft_b, lam) where the trainer should
    compute loss = lam * loss(pred, y_a) + (1-lam) * loss(pred, y_b).

    For CORN, the target is per-conditional-level binary, so:
      target_at_level_k = (y_ordinal >= k+1).float()
    interpolating these with Mixup preserves the rank-consistency property
    (a convex combination of two rank-consistent targets is rank-consistent).
    """
    if alpha <= 0:
        return x, y_ordinal, y_ordinal, 1.0
    lam = float(np.random.beta(alpha, alpha))
    perm = torch.randperm(x.size(0), device=x.device)
    x_mix = lam * x + (1.0 - lam) * x[perm]
    return x_mix, y_ordinal, y_ordinal[perm], lam


def corn_loss_with_mixup(conditional_logits: torch.Tensor,
                            y_a: torch.Tensor, y_b: torch.Tensor,
                            lam: float, n_thresholds: int) -> torch.Tensor:
    """Compute CORN loss with Mixup label interpolation."""
    if lam >= 1.0 - 1e-6:
        return corn_loss(conditional_logits, y_a, n_thresholds)
    loss_a = corn_loss(conditional_logits, y_a, n_thresholds)
    loss_b = corn_loss(conditional_logits, y_b, n_thresholds)
    return lam * loss_a + (1.0 - lam) * loss_b
