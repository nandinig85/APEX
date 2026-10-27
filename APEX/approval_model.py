"""Lender-specific approval-probability model: a calibrated logistic scorecard.

This is the standard credit-scorecard pipeline used in retail lending:

1. Features are derived by the APEX engine for each historical application.
2. Each feature is binned (quantiles), with a separate "missing" bin; adjacent
   bins are merged until Weight of Evidence is monotone in the expected
   direction (e.g. higher FOIR never raises approval likelihood).
       WoE_b = ln( (g_b + a) / (G + a m)  /  (b_b + a) / (B + a m) )
   where g/b = approved/declined counts, a = 0.5 smoothing, m = number of bins.
   Information Value IV = sum_b (g_b/G - b_b/B) WoE_b.
3. A logistic regression on the WoE values is fitted by iteratively reweighted
   least squares with an L2 penalty (Newton's method on the penalised
   log-likelihood); its covariance (X'WX + L)^-1 gives standard errors.
   Features whose coefficient comes out negative (contrary to WoE direction) or
   whose IV < 0.02 are dropped and the model refitted.
4. Scorecard scaling: score = offset + factor * logit, factor = PDO / ln 2,
   offset = base_score - factor * ln(base_odds).
5. Calibration on a later, held-out period (Platt scaling or isotonic
   regression via pool-adjacent-violators).
6. Evaluation on the most recent, untouched period (out-of-time): AUC, Gini,
   KS, Brier score, log-loss, expected calibration error, a reliability table,
   PSI between training and test score distributions, and subgroup approval
   rates / adverse-impact ratios by employment category and age band.
7. A model is used only if it passes the bank's ``model_gate`` (sample size,
   events per class, AUC, calibration error).

The probability estimates how likely THIS BANK was to approve similar
applications in its own history. It inherits any bias in those past decisions,
which is why subgroup monitoring is part of every training run. It never
overrides a rule outcome.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from models import ApplicantInput, Assessment, BankConfig, ProbabilityEstimate

MODEL_DIR = Path(os.environ.get("APEX_MODEL_DIR", Path(__file__).resolve().parent / "model_store"))
SMOOTH = 0.5
Z95 = 1.959963984540054


@dataclass(frozen=True)
class Feature:
    name: str
    label: str
    direction: int   # +1: higher value -> more likely approved; -1: less likely


FEATURES = [
    Feature("foir", "FOIR", -1),
    Feature("credit_score", "Credit score", +1),
    Feature("credit_utilisation", "Credit utilisation", -1),
    Feature("loan_to_income", "Loan-to-income", -1),
    Feature("years_in_employment", "Years in employment", +1),
    Feature("income_stability", "Income stability", +1),
    Feature("residual_share", "Residual income share", +1),
    Feature("documentation_share", "Documents available", +1),
    Feature("max_dpd_24m", "Max DPD (24 months)", -1),
    Feature("enquiries_6m", "Enquiries (6 months)", -1),
    Feature("credit_history_months", "Credit history length", +1),
    Feature("emi_bounces_6m", "EMI bounces (6 months)", -1),
    Feature("shortfall_probability", "Monthly P(FOIR > cap)", -1),
]
FEATURE_BY_NAME = {f.name: f for f in FEATURES}


def feature_vector(r: Assessment) -> dict[str, Optional[float]]:
    a = r.applicant
    m = r.metrics
    adj = r.income.adjusted_income
    docs = next((f for f in r.factors if f.factor == "documentation"), None)
    score = a.credit_score if a.credit_score is not None else a.credit_score_range_low
    return {
        "foir": m["foir"].value,
        "credit_score": None if a.no_credit_history else score,
        "credit_utilisation": m["credit_utilisation"].value,
        "loan_to_income": m["loan_to_income"].value,
        "years_in_employment": a.years_in_employment,
        "income_stability": m["income_stability"].value,
        "residual_share": (m["residual_income"].value / adj) if adj and m["residual_income"].value is not None else None,
        "documentation_share": (docs.score / 100) if docs and docs.status.value == "scored" else None,
        "max_dpd_24m": a.max_dpd_24m,
        "enquiries_6m": a.enquiries_6m,
        "credit_history_months": a.credit_history_months,
        "emi_bounces_6m": a.emi_bounces_6m,
        "shortfall_probability": r.stress.shortfall_probability,
    }


# --------------------------------------------------------------------------
# Binning and Weight of Evidence
# --------------------------------------------------------------------------
def _woe_counts(good: np.ndarray, bad: np.ndarray, G: float, B: float) -> np.ndarray:
    m = len(good)
    return np.log(((good + SMOOTH) / (G + SMOOTH * m)) / ((bad + SMOOTH) / (B + SMOOTH * m)))


def fit_bins(x: np.ndarray, y: np.ndarray, n_bins: int, direction: int, min_frac: float = 0.05) -> dict:
    """Quantile bins merged until WoE is monotone in ``direction`` and every bin holds >= min_frac."""
    miss = np.isnan(x)
    xv, yv = x[~miss], y[~miss]
    G, B = float(y.sum()), float(len(y) - y.sum())
    edges: list[float] = []
    if len(xv):
        qs = np.quantile(xv, np.linspace(0, 1, n_bins + 1)[1:-1])
        edges = sorted(set(float(q) for q in qs))

    def stats(edges):
        idx = np.searchsorted(np.asarray(edges), xv, side="right")
        k = len(edges) + 1
        good = np.array([yv[idx == i].sum() for i in range(k)], dtype=float)
        tot = np.array([(idx == i).sum() for i in range(k)], dtype=float)
        return good, tot - good, tot

    while True:
        good, bad, tot = stats(edges)
        woe = _woe_counts(good, bad, G, B)
        if len(edges) == 0:
            break
        small = np.where(tot < min_frac * len(y))[0]
        if len(small):
            i = int(small[0])
            edges.pop(min(i, len(edges) - 1))   # merge the small bin with its right (or left) neighbour
            continue
        viol = [i for i in range(len(woe) - 1) if direction and (woe[i + 1] - woe[i]) * direction < 0]
        if not viol:
            break
        edges.pop(viol[0])
    g_m, t_m = float(y[miss].sum()), float(miss.sum())
    woe_missing = float(_woe_counts(np.array([g_m]), np.array([t_m - g_m]), G, B)[0]) if t_m >= min_frac * len(y) else None
    gp, bp = good / G, bad / B
    iv = float(np.sum((gp - bp) * woe))
    if woe_missing is not None:
        iv += float((g_m / G - (t_m - g_m) / B) * woe_missing)
    return {"edges": edges, "woe": woe.tolist(), "woe_missing": woe_missing, "counts": tot.tolist(),
            "approval_rate": (good / np.maximum(tot, 1)).tolist(), "missing_count": t_m, "iv": iv}


def woe_transform(x: np.ndarray, bins: dict) -> np.ndarray:
    woe = np.asarray(bins["woe"])
    out = np.empty(len(x))
    miss = np.isnan(x)
    out[~miss] = woe[np.searchsorted(np.asarray(bins["edges"]), x[~miss], side="right")]
    # A missing bin unseen in training gets the least favourable WoE (conservative).
    out[miss] = bins["woe_missing"] if bins["woe_missing"] is not None else float(woe.min())
    return out


def bin_label(bins: dict, i: Optional[int]) -> str:
    if i is None:
        return "missing"
    e = bins["edges"]
    if not e:
        return "all values"
    if i == 0:
        return f"< {e[0]:.4g}"
    if i == len(e):
        return f">= {e[-1]:.4g}"
    return f"[{e[i - 1]:.4g}, {e[i]:.4g})"


def iv_strength(iv: float) -> str:
    return ("not predictive" if iv < 0.02 else "weak" if iv < 0.1 else "medium" if iv < 0.3
            else "strong" if iv < 0.5 else "suspiciously strong (check for leakage)")


# --------------------------------------------------------------------------
# Logistic regression (IRLS with L2 penalty)
# --------------------------------------------------------------------------
def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, max_iter: int = 100,
                 tol: float = 1e-9) -> tuple[np.ndarray, np.ndarray]:
    """Maximise sum[y log p + (1-y) log(1-p)] - (l2/2)||beta_1:||^2 by Newton-Raphson.

    Returns (beta incl. intercept, covariance = inverse penalised Hessian)."""
    X1 = np.column_stack([np.ones(len(X)), X])
    pen = np.diag([0.0] + [l2] * X.shape[1])
    beta = np.zeros(X1.shape[1])
    for _ in range(max_iter):
        p = sigmoid(X1 @ beta)
        w = p * (1 - p)
        grad = X1.T @ (y - p) - pen @ beta
        H = (X1 * w[:, None]).T @ X1 + pen
        step = np.linalg.solve(H, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    p = sigmoid(X1 @ beta)
    H = (X1 * (p * (1 - p))[:, None]).T @ X1 + pen
    return beta, np.linalg.inv(H)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
def fit_platt(z: np.ndarray, y: np.ndarray) -> dict:
    beta, _ = fit_logistic(z.reshape(-1, 1), y, l2=1e-6)
    return {"method": "platt", "a": float(beta[1]), "b": float(beta[0])}


def pav(y: np.ndarray, w: Optional[np.ndarray] = None) -> np.ndarray:
    """Pool-adjacent-violators: the non-decreasing sequence closest to y in weighted least squares."""
    w = np.ones(len(y)) if w is None else w.astype(float)
    vals, wts, sizes = [], [], []
    for yi, wi in zip(y.astype(float), w):
        vals.append(yi), wts.append(wi), sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / (wts[-2] + wts[-1])
            wsum, s = wts[-2] + wts[-1], sizes[-2] + sizes[-1]
            vals[-2:], wts[-2:], sizes[-2:] = [v], [wsum], [s]
    return np.repeat(vals, sizes)


def fit_isotonic(z: np.ndarray, y: np.ndarray) -> dict:
    order = np.argsort(z)
    fitted = pav(y[order])
    fitted = np.clip(fitted, 1e-4, 1 - 1e-4)
    return {"method": "isotonic", "x": z[order].tolist(), "y": fitted.tolist()}


def calibrate(z: np.ndarray | float, cal: dict) -> np.ndarray:
    z = np.asarray(z, dtype=float)
    if cal["method"] == "platt":
        return sigmoid(cal["a"] * z + cal["b"])
    return np.interp(z, cal["x"], cal["y"])


# --------------------------------------------------------------------------
# Evaluation metrics
# --------------------------------------------------------------------------
def _ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1   # average rank for ties
        i = j + 1
    return ranks


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """Mann-Whitney U / (n1 n0): probability a random approved case outscores a random declined one."""
    n1, n0 = int(y.sum()), int(len(y) - y.sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = _ranks(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def roc_points(y: np.ndarray, s: np.ndarray, k: int = 60) -> list[dict]:
    thr = np.unique(np.quantile(s, np.linspace(0, 1, k)))[::-1]
    P, N = y.sum(), len(y) - y.sum()
    pts = [{"fpr": 0.0, "tpr": 0.0}]
    for t in thr:
        pred = s >= t
        pts.append({"fpr": float((pred & (y == 0)).sum() / N), "tpr": float((pred & (y == 1)).sum() / P)})
    pts.append({"fpr": 1.0, "tpr": 1.0})
    return pts


def ks_stat(y: np.ndarray, s: np.ndarray) -> float:
    order = np.argsort(-s)
    ys = y[order]
    tpr = np.cumsum(ys) / ys.sum()
    fpr = np.cumsum(1 - ys) / (len(ys) - ys.sum())
    return float(np.max(np.abs(tpr - fpr)))


def brier(y, p) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y, p) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def reliability(y, p, n_bins: int = 10) -> tuple[float, list[dict]]:
    """Expected calibration error over equal-width bins, plus the reliability table."""
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    ece, rows = 0.0, []
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() == 0:
            continue
        mp, ob = float(p[mask].mean()), float(y[mask].mean())
        ece += mask.mean() * abs(mp - ob)
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(mask.sum()), "mean_predicted": mp,
                     "observed_rate": ob})
    return float(ece), rows


def psi(expected: np.ndarray, actual: np.ndarray, n_bins: int = 10) -> float:
    """Population Stability Index: sum (a - e) ln(a / e) over bins of the expected distribution."""
    cuts = np.unique(np.quantile(expected, np.linspace(0, 1, n_bins + 1)[1:-1]))
    e = np.bincount(np.searchsorted(cuts, expected, side="right"), minlength=len(cuts) + 1) / len(expected)
    a = np.bincount(np.searchsorted(cuts, actual, side="right"), minlength=len(cuts) + 1) / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_label(v: float) -> str:
    return "stable" if v < 0.1 else "moderate shift" if v < 0.25 else "significant shift - retrain"


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def _age_band(age) -> str:
    if age is None or (isinstance(age, float) and math.isnan(age)):
        return "unknown"
    return "<30" if age < 30 else "30-44" if age < 45 else "45+"


def _row_to_applicant(row: dict, i: int) -> ApplicantInput:
    allowed = set(ApplicantInput.model_fields)
    data = {}
    for k, v in row.items():
        if k not in allowed:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        if k == "documents" and isinstance(v, str):
            v = json.loads(v)
        if k == "monthly_income_observations" and isinstance(v, str):
            v = json.loads(v) if v else None
        data[k] = v
    data.setdefault("applicant_id", f"HIST-{i}")
    data["applicant_id"] = str(data["applicant_id"])
    return ApplicantInput.model_validate(data)


def build_features(history: pd.DataFrame, cfg: BankConfig) -> pd.DataFrame:
    from engine import assess  # local import: engine uses this module for prediction
    rows = []
    for i, rec in enumerate(history.to_dict("records")):
        a = _row_to_applicant(rec, i)
        r = assess(a, cfg, timestamp="history", use_model=False)
        fv = feature_vector(r)
        fv.update(decision=int(rec["decision"]), decision_date=pd.Timestamp(rec["decision_date"]),
                  employment_category=a.employment_category.value if a.employment_category else "unknown",
                  age_band=_age_band(a.age))
        rows.append(fv)
    return pd.DataFrame(rows)


def train(history: pd.DataFrame, cfg: BankConfig, data_source: str, synthetic: bool, n_bins: int = 5,
          l2: float = 1.0, calibration: str = "platt", pdo: float = 20, base_score: float = 600,
          base_odds: float = 50) -> dict:
    """Train, calibrate and validate a scorecard on a bank's historical decisions.

    ``history`` needs ApplicantInput columns plus ``decision`` (1 approved,
    0 declined) and ``decision_date``. The split is chronological:
    oldest 60 % train, next 20 % calibration, newest 20 % test (out-of-time)."""
    required = {"decision", "decision_date"}
    if required - set(history.columns):
        raise ValueError(f"history needs columns {sorted(required)}")
    if not set(history["decision"].dropna().unique()) <= {0, 1}:
        raise ValueError("decision must be 0 (declined) or 1 (approved)")
    feats = build_features(history, cfg).sort_values("decision_date").reset_index(drop=True)
    n = len(feats)
    i1, i2 = int(0.6 * n), int(0.8 * n)
    tr, ca, te = feats.iloc[:i1], feats.iloc[i1:i2], feats.iloc[i2:]
    ytr, yca, yte = (d["decision"].to_numpy(float) for d in (tr, ca, te))

    def col(df, f):
        return df[f].astype(float).to_numpy()

    bins = {f.name: fit_bins(col(tr, f.name), ytr, n_bins, f.direction) for f in FEATURES}
    selected = [f.name for f in FEATURES if bins[f.name]["iv"] >= 0.02]
    dropped = {f.name: "IV < 0.02" for f in FEATURES if f.name not in selected}
    while True:
        Xtr = np.column_stack([woe_transform(col(tr, f), bins[f]) for f in selected]) if selected else np.zeros((len(tr), 0))
        beta, cov = fit_logistic(Xtr, ytr, l2=l2)
        neg = [f for f, b in zip(selected, beta[1:]) if b < 0]
        if not neg:
            break
        worst = min(neg, key=lambda f: beta[1 + selected.index(f)])
        dropped[worst] = "coefficient sign contradicts WoE (collinear)"
        selected.remove(worst)

    def logit(df):
        X = np.column_stack([np.ones(len(df))] + [woe_transform(col(df, f), bins[f]) for f in selected])
        return X @ beta

    cal = fit_platt(logit(ca), yca) if calibration == "platt" else fit_isotonic(logit(ca), yca)
    z_tr, z_te = logit(tr), logit(te)
    p_te = calibrate(z_te, cal)
    ece, rel = reliability(yte, p_te)
    metrics = {
        "train_auc": auc(ytr, z_tr), "test_auc": auc(yte, z_te), "test_gini": 2 * auc(yte, z_te) - 1,
        "test_ks": ks_stat(yte, z_te), "test_brier": brier(yte, p_te), "test_log_loss": log_loss(yte, p_te),
        "test_ece": ece, "psi_train_vs_test": psi(z_tr, z_te),
        "base_rate_train": float(ytr.mean()), "base_rate_test": float(yte.mean()),
    }
    # Subgroup monitoring on the test period (attributes are NOT model inputs).
    te = te.assign(p=p_te)
    subgroups = []
    for attr in ("employment_category", "age_band"):
        rates = te.groupby(attr)["decision"].mean()
        best = rates.max() if len(rates) else 1
        for g, d in te.groupby(attr):
            y_g = d["decision"].to_numpy(float)
            subgroups.append({"attribute": attr, "group": g, "n": len(d), "observed_approval_rate": float(y_g.mean()),
                              "mean_predicted": float(d["p"].mean()),
                              "auc": auc(y_g, d["p"].to_numpy()) if 0 < y_g.sum() < len(y_g) else None,
                              "adverse_impact_ratio": float(y_g.mean() / best) if best else None})
    gate = cfg.model_gate
    reasons = []
    if len(tr) < gate.min_samples:
        reasons.append(f"training sample {len(tr)} < {gate.min_samples}")
    if min(ytr.sum(), len(ytr) - ytr.sum()) < gate.min_events_per_class:
        reasons.append(f"fewer than {gate.min_events_per_class} approvals or declines in training")
    if not metrics["test_auc"] >= gate.min_test_auc:
        reasons.append(f"test AUC {metrics['test_auc']:.3f} < {gate.min_test_auc}")
    if not ece <= gate.max_test_ece:
        reasons.append(f"test calibration error {ece:.3f} > {gate.max_test_ece}")
    factor = pdo / math.log(2)
    offset = base_score - factor * math.log(base_odds)
    scorecard = []
    for j, f in enumerate(selected):
        b = bins[f]
        for i, w in enumerate(b["woe"]):
            scorecard.append({"feature": f, "label": FEATURE_BY_NAME[f].label, "bin": bin_label(b, i),
                              "woe": w, "points": factor * beta[1 + j] * w, "train_n": b["counts"][i]})
        wm = b["woe_missing"] if b["woe_missing"] is not None else min(b["woe"])
        scorecard.append({"feature": f, "label": FEATURE_BY_NAME[f].label, "bin": "missing", "woe": wm,
                          "points": factor * beta[1 + j] * wm, "train_n": b["missing_count"]})
    digest = hashlib.sha256(pd.util.hash_pandas_object(history.astype(str), index=False).values.tobytes()).hexdigest()
    return {
        "model_id": f"{cfg.meta.bank_id}-scorecard-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "bank_id": cfg.meta.bank_id, "policy_version_at_training": cfg.meta.policy_version,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_source": data_source, "synthetic": synthetic, "data_sha256": digest,
        "n_total": n, "n_train": len(tr), "n_calibration": len(ca), "n_test": len(te),
        "period_train": [str(tr["decision_date"].min().date()), str(tr["decision_date"].max().date())],
        "period_test": [str(te["decision_date"].min().date()), str(te["decision_date"].max().date())],
        "hyperparameters": {"n_bins": n_bins, "l2": l2, "calibration": calibration, "pdo": pdo,
                            "base_score": base_score, "base_odds": base_odds},
        "features": selected, "dropped": dropped,
        "bins": bins, "iv": {f: bins[f]["iv"] for f in bins},
        "beta": beta.tolist(), "cov": cov.tolist(), "calibration": cal,
        "scaling": {"factor": factor, "offset": offset},
        "scorecard": scorecard, "base_points": offset + factor * beta[0],
        "metrics": metrics, "reliability": rel, "roc": roc_points(yte, z_te), "subgroups": subgroups,
        "gate": {"passed": not reasons, "reasons": reasons},
    }


# --------------------------------------------------------------------------
# Persistence and prediction
# --------------------------------------------------------------------------
def model_path(bank_id: str) -> Path:
    return MODEL_DIR / f"{bank_id}.json"


def save_model(model: dict) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    p = model_path(model["bank_id"])
    p.write_text(json.dumps(model), encoding="utf-8")
    return p


_cache: dict[str, tuple[float, dict]] = {}


def load_model(bank_id: str) -> Optional[dict]:
    p = model_path(bank_id)
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    hit = _cache.get(bank_id)
    if hit and hit[0] == mtime:
        return hit[1]
    model = json.loads(p.read_text(encoding="utf-8"))
    _cache[bank_id] = (mtime, model)
    return model


def delete_model(bank_id: str) -> None:
    p = model_path(bank_id)
    if p.exists():
        p.unlink()
    _cache.pop(bank_id, None)


def predict(model: dict, r: Assessment, cfg: BankConfig) -> Optional[ProbabilityEstimate]:
    """Calibrated probability with a 95 % interval from the delta method on the logit:
    Var(z) = x' Sigma x, interval = calibrate(z +/- 1.96 sd)."""
    # A policy change alters the features and the population the model was
    # validated against. A warning beside a live probability is insufficient.
    if (not model["gate"]["passed"] or model.get("bank_id") != cfg.meta.bank_id
            or model.get("policy_version_at_training") != cfg.meta.policy_version):
        return None
    fv = feature_vector(r)
    feats = model["features"]
    w = [float(woe_transform(np.array([np.nan if fv[f] is None else float(fv[f])]), model["bins"][f])[0])
         for f in feats]
    x = np.array([1.0] + w)
    beta, cov = np.array(model["beta"]), np.array(model["cov"])
    z = float(x @ beta)
    sd = float(math.sqrt(max(0.0, x @ cov @ x)))
    cal = model["calibration"]
    p, lo, hi = (float(calibrate(v, cal)) for v in (z, z - Z95 * sd, z + Z95 * sd))
    sc = model["scaling"]
    # Reason codes: features whose bin earns the most points below that feature's best bin.
    reasons = []
    for j, f in enumerate(feats):
        pts = sc["factor"] * beta[1 + j] * w[j]
        best = max(row["points"] for row in model["scorecard"] if row["feature"] == f)
        reasons.append((best - pts, f, fv[f]))
    reasons.sort(reverse=True)
    codes = []
    for gap, f, v in reasons[:4]:
        if gap < 1:
            continue
        shown = "missing" if v is None else (f"{v:.1%}" if f in ("foir", "credit_utilisation", "residual_share",
                                                                    "documentation_share", "shortfall_probability")
                                             else f"{v:.4g}")
        codes.append(f"{FEATURE_BY_NAME[f].label} ({shown}): {gap:.0f} points below the best band")
    return ProbabilityEstimate(
        probability=p, lower_95=min(lo, hi), upper_95=max(lo, hi),
        scorecard_points=sc["offset"] + sc["factor"] * z, model_id=model["model_id"],
        trained_on=(f"{model['data_source']} (demonstration only)" if model["synthetic"]
                    else model["data_source"]),
        synthetic=model["synthetic"], n_train=model["n_train"], test_auc=model["metrics"]["test_auc"],
        test_ece=model["metrics"]["test_ece"], policy_version_at_training=model["policy_version_at_training"],
        policy_changed_since_training=model["policy_version_at_training"] != cfg.meta.policy_version,
        reason_codes=codes)


class ActiveModelPredictor:
    """Uses the saved, gate-passing model for the selected bank, if any."""
    name = "active-scorecard"

    def predict(self, r: Assessment, cfg: BankConfig) -> Optional[ProbabilityEstimate]:
        model = load_model(cfg.meta.bank_id)
        return None if model is None else predict(model, r, cfg)
