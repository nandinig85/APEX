"""APEX bank-manager interface (Streamlit).

The UI only collects inputs and renders results; every number comes from the
engine (engine.assess), the policy-rule engine and the approval model.
Run with:  streamlit run app.py
"""
from __future__ import annotations

import html
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

import approval_model as am
import bundle
import calculations as calc
from advisor import advise
from eligibility import CONFIG_DIR, available_banks
from engine import DISCLAIMER, assess, compute_capacity, inr
from kyc import HOLDER_TYPES, mask_pan
from models import (DOCUMENT_TYPES, EMPLOYMENT_LABELS, STAGES, ApplicantInput, Assessment, CheckStatus,
                    Classification, DocStatus, EmployerCategory, EmploymentCategory, FactorStatus,
                    IncomeVariability, PanStatus, PolicyRule)
from policy_rules import (FIELD_REGISTRY, OPERATORS_BY_KIND, RuleError, check_field_allowed, read_audit_log,
                          requirement_text, save_overlay, validate_rule)
from pricing import recommend_rate
from profiles import PROFILES, PROFILES_BY_KEY
from reports import customer_letter, report_json, report_markdown
from synthetic_history import generate

st.set_page_config(page_title="APEX - Loan Decision Support", page_icon="▲", layout="wide")

PAGES = ["Customer information", "Assessment dashboard", "Decision & advice", "Explanation & diagnosis",
         "Loan simulator", "Reports & customer letter", "Policy studio", "Model lab", "Guide"]
PAGE_ICON = dict(zip(PAGES, ["📝", "📊", "🎯", "🔍", "🧮", "📄", "⚙️", "🧪", "📘"]))
COLORS = {"green": "#15803d", "amber": "#b45309", "red": "#b91c1c", "grey": "#64748b", "info": "#1d4ed8",
          "purple": "#7c3aed"}
TINT = {"green": "#ecfdf3", "amber": "#fff7ed", "red": "#fef2f2", "grey": "#f1f5f9", "info": "#eff6ff",
        "purple": "#f5f3ff"}
IND_TEXT = {"green": "Meets configured criterion", "amber": "Requires review", "red": "Does not meet criterion",
            "grey": "Missing / not applicable", "info": "Informational"}
STATUS_COLOR = {CheckStatus.PASS: "green", CheckStatus.FAIL: "red", CheckStatus.REVIEW: "amber",
                CheckStatus.DEVIATION: "purple", CheckStatus.NOT_APPLICABLE: "grey"}
CLASS_COLOR = {Classification.LIKELY: "green", Classification.BORDERLINE: "amber",
               Classification.UNLIKELY: "red", Classification.INSUFFICIENT: "grey"}
CAT_OPTIONS = ["Not provided"] + [c.value for c in EmploymentCategory]
EMPR_OPTIONS = ["Not recorded"] + [c.value for c in EmployerCategory]
TRI = ["Not recorded", "Yes", "No"]
TRI_VAL = {"Not recorded": None, "Yes": True, "No": False}
SCORE_MODES = ["Not known", "Exact score", "Score range", "No credit history (new to credit)"]
DOC_OPTIONS = [s.value for s in DocStatus]
OP_TEXT = {"<": "less than", "<=": "at most", ">": "greater than", ">=": "at least", "==": "equal to",
           "!=": "not equal to", "in": "one of", "not_in": "none of", "is_true": "is Yes", "is_false": "is No"}

st.markdown("""
<style>
.block-container{padding-top:3.5rem;padding-bottom:3rem;max-width:1440px}
h2,h3,h4{color:#0f172a;letter-spacing:-.01em}
.block-container h3{border-left:4px solid #1e40af;padding-left:10px;margin-top:1.1rem}
section[data-testid="stSidebar"]{background:linear-gradient(180deg,#0b1f4d 0%,#13306f 100%)}
section[data-testid="stSidebar"] *{color:#e2e8f0}
section[data-testid="stSidebar"] .stSelectbox .react-aria-ComboBox>div,
section[data-testid="stSidebar"] [data-baseweb="select"]>div{background:rgba(255,255,255,.10)!important;
  border-color:rgba(255,255,255,.28)!important}
section[data-testid="stSidebar"] .stSelectbox input{color:#f8fafc!important;-webkit-text-fill-color:#f8fafc}
section[data-testid="stSidebar"] .stSelectbox svg{fill:#f8fafc;color:#f8fafc}
section[data-testid="stSidebar"] button *{color:#0f172a}
section[data-testid="stSidebar"] hr{border-color:rgba(255,255,255,.15)}
.ax-brand{display:flex;align-items:center;gap:10px;margin:2px 0 6px}
.ax-logo{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,#22d3ee,#6366f1);
         display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:1.1rem}
.ax-word{font-size:1.45rem;font-weight:800;letter-spacing:.18em;color:#fff}
.ax-tag{font-size:.72rem;opacity:.75;margin-top:-2px}
.ax-hero{background:linear-gradient(115deg,#0b1f4d 0%,#1e3a8a 55%,#0e7490 100%);color:#fff;border-radius:16px;
         padding:16px 22px;margin:0 0 16px;display:flex;justify-content:space-between;align-items:center;
         gap:12px;flex-wrap:wrap;box-shadow:0 6px 18px rgba(15,23,42,.12)}
.ax-hero .t{font-size:1.45rem;font-weight:750;line-height:1.2}
.ax-hero .s{font-size:.84rem;opacity:.85;margin-top:2px}
.ax-chip{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.74rem;font-weight:650;
         background:rgba(255,255,255,.16);color:#fff;margin:2px 0 2px 6px;white-space:nowrap}
.ax-card{background:#fff;border:1px solid #e2e8f0;border-top:4px solid #cbd5e1;border-radius:14px;
         padding:12px 14px;margin-bottom:14px;min-height:150px;box-shadow:0 1px 3px rgba(15,23,42,.05)}
.ax-label{font-size:.78rem;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.ax-value{font-size:1.38rem;font-weight:750;margin:4px 0 2px;color:#0f172a}
.ax-status{font-size:.78rem;font-weight:650}
.ax-note{font-size:.75rem;color:#64748b;margin-top:4px}
.ax-ev{font-size:.68rem;color:#94a3b8;margin-top:4px;font-style:italic}
.ax-banner{border-radius:14px;padding:14px 18px;margin:0 0 14px;border:1px solid #e2e8f0;background:#fff;
           box-shadow:0 1px 3px rgba(15,23,42,.05)}
.ax-pill{display:inline-block;padding:2px 9px;border-radius:999px;color:#fff;font-size:.72rem;font-weight:650}
.ax-stage{background:#fff;border:1px solid #e2e8f0;border-top:5px solid;border-radius:12px;padding:8px 10px;
          font-size:.78rem;min-height:78px}
.ax-gauge{position:relative;height:12px;border-radius:999px;overflow:hidden;display:flex;margin:8px 0 4px}
.ax-gauge div{height:100%}
.ax-mark{position:absolute;top:-4px;width:4px;height:20px;background:#0f172a;border-radius:2px}
.ax-kpi{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:12px 14px;height:100%}
.ax-kpi .k{font-size:.74rem;color:#64748b;font-weight:650;text-transform:uppercase;letter-spacing:.04em}
.ax-kpi .v{font-size:1.5rem;font-weight:800;color:#0f172a;margin-top:2px}
.ax-kpi .n{font-size:.75rem;color:#64748b}
.ax-reason{border-radius:12px;padding:10px 12px;margin-bottom:8px;border-left:5px solid;font-size:.88rem}
.ax-reason b{display:block;margin-bottom:2px}
.ax-option{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px;height:100%;
           box-shadow:0 1px 3px rgba(15,23,42,.05)}
.ax-option.rec{border:2px solid #15803d;box-shadow:0 4px 14px rgba(21,128,61,.15)}
.ax-option .h{font-weight:750;font-size:.98rem;margin-bottom:4px}
.ax-option .d{font-size:.8rem;color:#64748b;margin-bottom:8px;min-height:34px}
.ax-option table{width:100%;font-size:.82rem}
.ax-option td{padding:2px 0}
.ax-option td:last-child{text-align:right;font-weight:650}
.ax-step{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:12px 14px;margin-bottom:10px;
         display:flex;gap:14px;align-items:flex-start}
.ax-num{min-width:30px;height:30px;border-radius:50%;background:#1e40af;color:#fff;font-weight:750;
        display:flex;align-items:center;justify-content:center}
.ax-step .a{font-weight:700}
.ax-step .m{font-size:.8rem;color:#64748b}
.ax-delta{display:inline-block;margin:4px 6px 0 0;padding:2px 8px;border-radius:8px;font-size:.75rem;font-weight:650;
          background:#ecfdf3;color:#15803d}
.ax-delta.flat{background:#f1f5f9;color:#64748b}
table.ax{border-collapse:collapse;width:100%;font-size:.85rem;background:#fff;border-radius:12px;overflow:hidden}
table.ax th{background:#f1f5f9;color:#334155;font-weight:650}
table.ax th,table.ax td{border-bottom:1px solid #e2e8f0;padding:7px 9px;text-align:left;vertical-align:top}
div[data-testid="stMetric"]{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:10px 12px}
</style>""", unsafe_allow_html=True)

# Loaded on every run (a few small YAML files) so config and Policy Studio edits apply immediately.
BANKS = available_banks()
ss = st.session_state


# --------------------------------------------------------------------------
# Form state <-> ApplicantInput
# --------------------------------------------------------------------------
def default_state() -> dict:
    d = dict(in_id="NEW-0001", in_age=None, in_cat="Not provided", in_amount=500_000.0, in_tenure=36,
             in_rate=float(next(iter(BANKS.values())).amount.interest_rate_base),
             in_purpose="", in_income=None, in_years=None, in_var="unknown", in_verified=None,
             in_obs="", in_cont="Not recorded", in_other=None, in_emis=None, in_loans=None, in_card="Not recorded",
             in_limit=None, in_balance=None, in_score_mode="Not known", in_score=None, in_lo=None, in_hi=None,
             in_pan="", in_pan_consent=False, in_pan_status="not_verified",
             in_dpd12=None, in_dpd24=None, in_wo="Not recorded", in_enq3=None, in_enq6=None, in_hist=None,
             in_unsec=None, in_bounce=None, in_abb=None, in_salreg="Not recorded", in_empr="Not recorded",
             in_existing="Not recorded", in_neg="Not recorded", in_ratetype="fixed", in_fee=None, in_otherchg=None)
    d.update({f"doc_{k}": "unknown" for k in DOCUMENT_TYPES})
    return d


def tri(v):
    return "Not recorded" if v is None else ("Yes" if v else "No")


def applicant_to_state(a: ApplicantInput) -> None:
    f = lambda x: None if x is None else float(x)  # noqa: E731
    if a.no_credit_history:
        mode = SCORE_MODES[3]
    elif a.credit_score is not None:
        mode = SCORE_MODES[1]
    elif a.credit_score_range_low is not None:
        mode = SCORE_MODES[2]
    else:
        mode = SCORE_MODES[0]
    ss.update(dict(
        in_id=a.applicant_id, in_age=a.age, in_cat=a.employment_category.value if a.employment_category else "Not provided",
        in_amount=float(a.requested_amount), in_tenure=a.tenure_months, in_rate=float(a.annual_interest_rate),
        in_purpose=a.loan_purpose or "", in_income=f(a.net_monthly_income), in_years=f(a.years_in_employment),
        in_var=a.income_variability.value, in_verified=f(a.verified_monthly_income),
        in_obs=", ".join(f"{x:.0f}" for x in a.monthly_income_observations or []),
        in_cont=tri(a.continuous_employment_12m), in_other=f(a.other_recurring_obligations),
        in_emis=f(a.existing_monthly_emis), in_loans=a.active_loan_count, in_card=tri(a.has_credit_card),
        in_limit=f(a.credit_card_limit), in_balance=f(a.credit_card_balance), in_score_mode=mode,
        in_score=a.credit_score, in_lo=a.credit_score_range_low, in_hi=a.credit_score_range_high,
        in_pan=a.pan.get_secret_value() if a.pan else "", in_pan_consent=a.pan_consent,
        in_pan_status=a.pan_status.value, in_dpd12=a.max_dpd_12m, in_dpd24=a.max_dpd_24m,
        in_wo=tri(a.written_off_or_settled_36m), in_enq3=a.enquiries_3m, in_enq6=a.enquiries_6m,
        in_hist=a.credit_history_months, in_unsec=a.unsecured_loans_active, in_bounce=a.emi_bounces_6m,
        in_abb=f(a.avg_monthly_balance), in_salreg=tri(a.salary_credit_regular),
        in_empr=a.employer_category.value if a.employer_category else "Not recorded",
        in_existing=tri(a.existing_customer), in_neg=tri(a.internal_negative_list_hit), in_ratetype=a.rate_type,
        in_fee=f(a.processing_fee_pct), in_otherchg=f(a.other_upfront_charges)))
    ss.update({f"doc_{k}": a.doc(k).value for k in DOCUMENT_TYPES})


def state_to_applicant() -> ApplicantInput:
    obs_txt = (ss.in_obs or "").strip()
    try:
        obs = [float(x) for x in re.split(r"[,\s;]+", obs_txt) if x] if obs_txt else None
    except ValueError:
        raise ValueError("Monthly income observations must be numbers separated by commas")
    mode = ss.in_score_mode
    data = dict(
        applicant_id=ss.in_id.strip(), age=ss.in_age,
        employment_category=None if ss.in_cat == "Not provided" else ss.in_cat,
        requested_amount=ss.in_amount, tenure_months=ss.in_tenure, annual_interest_rate=ss.in_rate,
        loan_purpose=ss.in_purpose or None, net_monthly_income=ss.in_income, years_in_employment=ss.in_years,
        income_variability=ss.in_var, verified_monthly_income=ss.in_verified, monthly_income_observations=obs,
        continuous_employment_12m=TRI_VAL[ss.in_cont], other_recurring_obligations=ss.in_other,
        existing_monthly_emis=ss.in_emis, active_loan_count=ss.in_loans, has_credit_card=TRI_VAL[ss.in_card],
        credit_card_limit=ss.in_limit if ss.in_card != "No" else None,
        credit_card_balance=ss.in_balance if ss.in_card != "No" else None,
        credit_score=ss.in_score if mode == SCORE_MODES[1] else None,
        credit_score_range_low=ss.in_lo if mode == SCORE_MODES[2] else None,
        credit_score_range_high=ss.in_hi if mode == SCORE_MODES[2] else None,
        no_credit_history=mode == SCORE_MODES[3],
        documents={k: ss[f"doc_{k}"] for k in DOCUMENT_TYPES if ss[f"doc_{k}"] != "unknown"},
        pan=(ss.in_pan or None) if ss.in_pan_consent else None, pan_consent=ss.in_pan_consent,
        pan_status=ss.in_pan_status,
        max_dpd_12m=ss.in_dpd12, max_dpd_24m=ss.in_dpd24, written_off_or_settled_36m=TRI_VAL[ss.in_wo],
        enquiries_3m=ss.in_enq3, enquiries_6m=ss.in_enq6, credit_history_months=ss.in_hist,
        unsecured_loans_active=ss.in_unsec, emi_bounces_6m=ss.in_bounce, avg_monthly_balance=ss.in_abb,
        salary_credit_regular=TRI_VAL[ss.in_salreg],
        employer_category=None if ss.in_empr == "Not recorded" else ss.in_empr,
        existing_customer=TRI_VAL[ss.in_existing], internal_negative_list_hit=TRI_VAL[ss.in_neg],
        rate_type=ss.in_ratetype, processing_fee_pct=ss.in_fee, other_upfront_charges=ss.in_otherchg,
    )
    if ss.in_pan and not ss.in_pan_consent:
        raise ValueError("PAN entered but customer consent is not recorded. Tick the consent box or clear the PAN.")
    return ApplicantInput.model_validate(data)


def saved_applicant() -> ApplicantInput | None:
    # Stored as plain data (raw PAN kept in server-side session only) so it survives module reloads.
    return None if ss.applicant_data is None else ApplicantInput.model_validate(ss.applicant_data)


def save_applicant(goto: str | None = None):
    try:
        ss.applicant_data = state_to_applicant().to_state()
        ss.form_errors = []
        if goto:
            ss.page = goto
    except ValidationError as e:
        ss.form_errors = [f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in e.errors()]
    except ValueError as e:
        ss.form_errors = [str(e)]


def load_profile():
    p = PROFILES_BY_KEY[ss.profile_pick]
    applicant_to_state(p.applicant)
    ss.applicant_data = p.applicant.to_state()
    ss.form_errors = []
    ss.pop("sim_for", None)


for k, v in default_state().items():
    ss.setdefault(k, v)
# Streamlit discards a widget's state on runs where the widget is not drawn
# (e.g. while another page is shown). Re-assigning keeps form values alive.
for k in [k for k in ss if k.startswith(("in_", "doc_", "sim_", "pr_", "ml_"))]:
    ss[k] = ss[k]
ss.setdefault("applicant_data", None)
ss.setdefault("form_errors", [])
ss.setdefault("page", PAGES[0])
if ss.page not in PAGES:          # page names from an older session
    ss.page = PAGES[0]
ss.setdefault("assess_ts", {})


@st.cache_data(show_spinner=False, max_entries=2)
def _project_zip(signature: tuple) -> bytes:   # signature = file paths + mtimes, so edits rebuild the zip
    return bundle.project_zip()


def download_zip_button(where, key: str, primary: bool = False) -> None:
    sig = tuple((str(p), p.stat().st_mtime_ns) for p in bundle.project_files())
    where.download_button("⬇️ Download APEX (.zip)", _project_zip(sig), file_name=bundle.ZIP_NAME,
                          mime="application/zip", width="stretch", key=key,
                          type="primary" if primary else "secondary")


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown('<div class="ax-brand"><div class="ax-logo">▲</div><div><div class="ax-word">APEX</div>'
                '<div class="ax-tag">Loan eligibility &amp; decision support</div></div></div>',
                unsafe_allow_html=True)
    bank_id = st.selectbox("Bank configuration", list(BANKS), key="bank_id",
                           format_func=lambda b: BANKS[b].meta.display_name)
    cfg = BANKS[bank_id]
    model_meta = am.load_model(bank_id)
    model_line = ("No approval model" if model_meta is None else
                  "Approval model inactive: policy changed" if model_meta.get("policy_version_at_training") != cfg.meta.policy_version else
                  ("Approval model active" + (" (SYNTHETIC)" if model_meta["synthetic"] else ""))
                  if model_meta["gate"]["passed"] else "Approval model failed its gate")
    st.caption(f"Policy **{cfg.meta.policy_version}** · verified {cfg.meta.last_verified}  \n"
               f"{len(cfg.custom_rules)} custom rule(s) · {model_line}  \nFictional demonstration bank")
    st.radio("Navigate", PAGES, key="page", format_func=lambda p: f"{PAGE_ICON[p]}  {p}")
    st.divider()
    st.selectbox("Synthetic profile", [p.key for p in PROFILES], key="profile_pick",
                 format_func=lambda k: f"{k} - {PROFILES_BY_KEY[k].title}")
    st.button("Load profile", on_click=load_profile, width="stretch")
    st.divider()
    download_zip_button(st, "dl_sidebar")
    st.caption("All source, configs, docs, tests and one-click installers. See the Guide page to install.")
    with st.expander("Disclaimer"):
        st.caption(DISCLAIMER)


def current_assessment() -> Assessment | None:
    a = saved_applicant()
    if a is None:
        return None
    key = f"{bank_id}|{cfg.meta.policy_version}|{json.dumps(a.to_state(), sort_keys=True)}"
    ts = ss.assess_ts.setdefault(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return assess(a, cfg, timestamp=ts)


def need_applicant() -> Assessment | None:
    r = current_assessment()
    if r is None:
        st.info("Enter customer information on page 1 (or load a synthetic profile from the sidebar) first.")
    return r


def current_advice(r: Assessment):
    return advise(r, cfg)


def apply_illustrative_rate(rate: float) -> None:
    """Use the suggested scenario rate and re-assess the saved application."""
    if ss.applicant_data is None:
        return
    data = {**ss.applicant_data, "annual_interest_rate": rate}
    applicant = ApplicantInput.model_validate(data)
    ss.applicant_data = applicant.to_state()
    ss.in_rate = rate
    ss.pop("sim_for", None)


def hero(title: str, subtitle: str, r: Assessment | None = None) -> None:
    chips = ""
    if r is not None:
        chips = (f'<span class="ax-chip">{html.escape(r.applicant.applicant_id)}</span>'
                 f'<span class="ax-chip">{inr(r.applicant.requested_amount)} · {r.applicant.tenure_months} m</span>'
                 f'<span class="ax-chip" style="background:{COLORS[CLASS_COLOR[r.classification]]}">'
                 f'{html.escape(r.classification.value)}</span>')
    st.markdown(f'<div class="ax-hero"><div><div class="t">{html.escape(title)}</div>'
                f'<div class="s">{html.escape(subtitle)}</div></div><div>{chips}</div></div>',
                unsafe_allow_html=True)


def pill(text: str, color: str) -> str:
    return f'<span class="ax-pill" style="background:{COLORS[color]}">{html.escape(text)}</span>'


def kpi(label: str, value: str, note: str = "", color: str | None = None, small: bool = False) -> str:
    style = f' style="border-top:4px solid {COLORS[color]}"' if color else ""
    vstyle = ' style="font-size:1.05rem;line-height:1.35"' if small else ""
    return (f'<div class="ax-kpi"{style}><div class="k">{html.escape(label)}</div><div class="v"{vstyle}>{html.escape(value)}'
            f'</div><div class="n">{html.escape(note)}</div></div>')


def card(m) -> str:
    c = COLORS[m.indicator.value]
    return (f'<div class="ax-card" style="border-top-color:{c}">'
            f'<div class="ax-label">{html.escape(m.label)}</div>'
            f'<div class="ax-value">{html.escape(m.display)}</div>'
            f'<div class="ax-status" style="color:{c}">{IND_TEXT[m.indicator.value]}</div>'
            f'<div class="ax-note">{html.escape(m.note)}</div>'
            f'<div class="ax-ev">{html.escape(m.evidence.value)}</div></div>')


def gauge(score: float | None) -> str:
    """Readiness bar with the Unlikely / Borderline / Likely bands of the selected bank."""
    sc = cfg.scoring
    segs = [(sc.borderline_min, "#fecaca"), (sc.likely_min - sc.borderline_min, "#fde68a"), (100 - sc.likely_min, "#bbf7d0")]
    bar = "".join(f'<div style="width:{w}%;background:{c}"></div>' for w, c in segs)
    mark = "" if score is None else f'<div class="ax-mark" style="left:calc({score}% - 2px)"></div>'
    return f'<div class="ax-gauge">{bar}{mark}</div>'


def banner(r: Assessment) -> None:
    color = CLASS_COLOR[r.classification]
    score = "n/a" if r.readiness_score is None else f"{r.readiness_score:.0f} / 100"
    p = r.probability
    prob = ("" if p is None else
            f" · bank model approval estimate <b>{p.probability:.0%}</b> ({p.lower_95:.0%}-{p.upper_95:.0%})"
            + (" <b style='color:#b91c1c'>[synthetic model]</b>" if p.synthetic else ""))
    st.markdown(
        f'<div class="ax-banner" style="border-left:8px solid {COLORS[color]};background:{TINT[color]}">'
        f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px">'
        f'<div><div style="font-size:.78rem;color:#64748b">Preliminary classification · {html.escape(r.bank_name)} '
        f'· policy {html.escape(r.policy_version)}</div>'
        f'<div style="font-size:1.5rem;font-weight:800;color:{COLORS[color]}">{r.classification.value}</div></div>'
        f'<div style="min-width:260px;flex:0 1 360px"><div style="font-size:.78rem;color:#64748b">Readiness score '
        f'<b style="color:#0f172a">{score}</b> (rule-based, not a probability)</div>{gauge(r.readiness_score)}'
        f'<div style="display:flex;justify-content:space-between;font-size:.68rem;color:#64748b">'
        f'<span>Unlikely</span><span>Borderline {cfg.scoring.borderline_min:g}</span>'
        f'<span>Likely {cfg.scoring.likely_min:g}</span></div></div></div>'
        f'<div style="font-size:.82rem;margin-top:6px">Applicant <code>{html.escape(r.applicant.applicant_id)}</code>'
        f'{prob}</div></div>', unsafe_allow_html=True)


def html_table(headers, rows) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="ax"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def status_pill(c) -> str:
    text = c.status.value + (f" - {c.deviation_authority}" if c.deviation_authority else "")
    return pill(text, STATUS_COLOR[c.status])


def checks_table(r: Assessment) -> str:
    src = {"built_in": "Built-in", "policy_rule": "Bank rule", "custom_rule": "Custom rule"}
    return html_table(["Stage", "Check", "Source", "Status", "Observed", "Required", "Detail"],
                      [[html.escape(c.stage), html.escape(c.label), src[c.source], status_pill(c),
                        html.escape(c.observed), html.escape(c.threshold), html.escape(c.detail)] for c in r.checks])


def pipeline_strip(r: Assessment) -> None:
    cols = st.columns(len(r.pipeline))
    for col, s in zip(cols, r.pipeline):
        color = COLORS[STATUS_COLOR[s.status]]
        n = {k: v for k, v in s.counts.items() if v}
        detail = ", ".join(f"{v} {k.lower()}" for k, v in n.items())
        col.markdown(f'<div class="ax-stage" style="border-top-color:{color}"><b>{html.escape(s.stage)}</b><br>'
                     f'<span style="color:{color};font-weight:600">{s.status.value}</span><br>'
                     f'<span style="opacity:.7">{detail}</span></div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Page 1: Customer information
# --------------------------------------------------------------------------
def page_input():
    hero("Customer information", "Fields marked * are required for a complete assessment. Leave unknown fields empty: "
                                 "missing information is reported as such, never as a failure. Synthetic data only.")

    st.subheader("A. Personal and loan information")
    c1, c2, c3 = st.columns(3)
    c1.text_input("Applicant ID * (internal reference)", key="in_id", max_chars=40)
    c2.number_input("Age * (completed years)", min_value=18, max_value=100, step=1, key="in_age")
    c3.selectbox("Employment category *", CAT_OPTIONS, key="in_cat",
                 format_func=lambda v: EMPLOYMENT_LABELS[EmploymentCategory(v)] if v != "Not provided" else v)
    c1, c2, c3 = st.columns(3)
    c1.number_input("Requested loan amount (INR) *", min_value=1.0, step=10_000.0, format="%.0f", key="in_amount")
    c2.number_input("Requested tenure (months) *", min_value=1, max_value=480, step=6, key="in_tenure")
    c3.number_input("Assumed annual interest rate (%) *", min_value=0.0, max_value=60.0, step=0.25, key="in_rate",
                    help="Entered for this scenario. A fictional policy suggestion is shown after assessment.")
    st.caption(f"Rate is an assumption, not a quote. This fictional bank's example base rate is "
               f"{cfg.amount.interest_rate_base:g}% (illustrative range "
               f"{cfg.amount.interest_rate_low:g}%–{cfg.amount.interest_rate_high:g}%).")
    st.button("Use this bank's example base rate", on_click=lambda: ss.update(
        in_rate=float(cfg.amount.interest_rate_base)))
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Rate type", ["fixed", "floating"], key="in_ratetype",
                 help="Floating-rate loans are stress-tested with a rate shock.")
    c2.number_input(f"Processing fee (%) - default {cfg.charges.default_processing_fee_pct:g}%", min_value=0.0,
                    max_value=10.0, step=0.25, key="in_fee")
    c3.number_input("Other upfront charges (INR)", min_value=0.0, step=500.0, format="%.0f", key="in_otherchg")
    st.text_input("Loan purpose (recordkeeping only - never scored)", key="in_purpose", max_chars=200)

    st.subheader("B. KYC - PAN")
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.checkbox("Customer has consented to PAN collection and verification", key="in_pan_consent")
    c2.text_input("PAN (format AAAAA9999A)", key="in_pan", max_chars=10, type="password",
                  disabled=not ss.in_pan_consent,
                  help="Used only for KYC and de-duplication. Shown masked everywhere; never used in scoring.")
    c3.selectbox("PAN verification status (from the bank's KYC system)", [s.value for s in PanStatus],
                 key="in_pan_status", format_func=lambda v: v.replace("_", " ").capitalize())
    if ss.in_pan:
        raw = ss.in_pan.strip().upper()
        if re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", raw):
            st.caption(f"PAN recorded as XXXXX{raw[5:9]}X - holder type {raw[3]} "
                       f"({HOLDER_TYPES.get(raw[3], 'unknown')}).")
        else:
            st.caption(":red[PAN format looks invalid (expected AAAAA9999A).]")

    st.subheader("C. Income")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Net monthly income (INR) *", min_value=0.0, step=1_000.0, format="%.0f", key="in_income")
    c2.number_input("Years in current employment / business *", min_value=0.0, max_value=60.0, step=0.5,
                    key="in_years")
    c3.number_input("Verified / documented monthly income (INR)", min_value=0.0, step=1_000.0, format="%.0f",
                    key="in_verified", help="Leave empty if income has not been verified against documents.")
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Self-reported income variability", [v.value for v in IncomeVariability], key="in_var")
    c2.selectbox("Continuous employment in last 12 months", TRI, key="in_cont")
    c3.number_input("Other verified recurring obligations (INR / month)", min_value=0.0, step=1_000.0,
                    format="%.0f", key="in_other")
    st.text_area(f"Monthly income observations (INR, comma-separated; at least "
                 f"{cfg.income_adjustment.min_observation_months} months to be used)", key="in_obs", height=68,
                 help="For irregular income. Used for the coefficient of variation and the shortfall model.")

    st.subheader("D. Existing debt and credit bureau")
    st.caption("Enter from a consented bureau report. Leave blank if the report has not been pulled.")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Total existing monthly EMIs (INR) *", min_value=0.0, step=1_000.0, format="%.0f", key="in_emis",
                    help="Enter 0 if there are no existing loans.")
    c2.number_input("Number of active loans", min_value=0, max_value=100, step=1, key="in_loans")
    c3.number_input("Active unsecured loans", min_value=0, max_value=50, step=1, key="in_unsec")
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Credit score information", SCORE_MODES, key="in_score_mode")
    if ss.in_score_mode == SCORE_MODES[1]:
        c2.number_input("Credit score (300-900)", min_value=300, max_value=900, step=1, key="in_score")
    elif ss.in_score_mode == SCORE_MODES[2]:
        c2.number_input("Score range - low", min_value=300, max_value=900, step=10, key="in_lo")
        c3.number_input("Score range - high", min_value=300, max_value=900, step=10, key="in_hi")
    c1, c2, c3, c4 = st.columns(4)
    c1.number_input("Max DPD, last 12 months (days)", min_value=0, max_value=999, step=15, key="in_dpd12")
    c2.number_input("Max DPD, last 24 months (days)", min_value=0, max_value=999, step=15, key="in_dpd24")
    c3.selectbox("Write-off / settlement in 36 months", TRI, key="in_wo")
    c4.number_input("Credit history length (months)", min_value=0, max_value=720, step=6, key="in_hist")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Bureau enquiries, last 3 months", min_value=0, max_value=100, step=1, key="in_enq3")
    c2.number_input("Bureau enquiries, last 6 months", min_value=0, max_value=100, step=1, key="in_enq6")
    c3.selectbox("Has a credit card?", TRI, key="in_card")
    if ss.in_card != "No":
        c1, c2 = st.columns(2)
        c1.number_input("Total credit card limit (INR)", min_value=0.0, step=5_000.0, format="%.0f", key="in_limit")
        c2.number_input("Outstanding credit card balance (INR)", min_value=0.0, step=1_000.0, format="%.0f",
                        key="in_balance")

    st.subheader("E. Bank-statement behaviour and relationship")
    c1, c2, c3 = st.columns(3)
    c1.number_input("EMI / NACH bounces, last 6 months", min_value=0, max_value=100, step=1, key="in_bounce")
    c2.number_input("Average monthly balance (INR)", min_value=0.0, step=1_000.0, format="%.0f", key="in_abb")
    c3.selectbox("Regular salary credits observed", TRI, key="in_salreg")
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Existing customer of this bank", TRI, key="in_existing")
    c2.selectbox("Internal negative-list hit", TRI, key="in_neg")
    c3.selectbox("Employer category (bank's list)", EMPR_OPTIONS, key="in_empr")

    st.subheader("F. Documentation")
    mandatory = []
    if ss.in_cat != "Not provided":
        mandatory = cfg.eligibility.mandatory_documents.get(EmploymentCategory(ss.in_cat), [])
        st.caption(f"Mandatory for this category under {cfg.meta.display_name}: "
                   + ", ".join(DOCUMENT_TYPES[d] for d in mandatory))
    cols = st.columns(4)
    for i, (k, label) in enumerate(DOCUMENT_TYPES.items()):
        cols[i % 4].selectbox(label + (" *" if k in mandatory else ""), DOC_OPTIONS, key=f"doc_{k}",
                              format_func=lambda v: v.replace("_", " ").capitalize())

    st.divider()
    b1, b2, _ = st.columns([1, 1, 3])
    b1.button("Save and assess", type="primary", on_click=save_applicant, args=(PAGES[1],), width="stretch")
    b2.button("Save only", on_click=save_applicant, width="stretch")
    for e in ss.form_errors:
        st.error(e)
    if ss.applicant_data is not None and not ss.form_errors:
        st.success(f"Saved applicant {ss.applicant_data['applicant_id']}. "
                   "Assessment pages reflect the last saved version of this form.")


# --------------------------------------------------------------------------
# Page 2: Loan assessment
# --------------------------------------------------------------------------
METRIC_ORDER = ["readiness_score", "indicative_max_loan", "requested_amount", "proposed_emi", "apr", "foir",
                "stressed_foir", "shortfall_probability", "credit_utilisation", "loan_to_income", "residual_income",
                "income_stability", "data_completeness", "adjusted_income"]


def probability_panel(r: Assessment) -> None:
    p = r.probability
    with st.container(border=True):
        st.markdown("**Approval probability - bank's own statistical model**")
        if p is None:
            st.caption("Not shown: this bank has no validated approval model. APEX only shows a probability from "
                       "a model trained on the bank's own past decisions that passed its validation gate. "
                       "Train one on page 7 (Model lab).")
            return
        c1, c2 = st.columns([1, 2])
        c1.metric("Estimated approval probability", f"{p.probability:.0%}",
                  help="How often this bank approved similar applications historically (calibrated).")
        c1.caption(f"95% interval {p.lower_95:.0%} - {p.upper_95:.0%}; scorecard {p.scorecard_points:.0f} points")
        with c2:
            fails = [c.label for c in r.checks if c.status == CheckStatus.FAIL]
            if fails:
                st.warning("Hard criteria failed (" + ", ".join(fails) + "). The rule outcome stands; the model "
                           "does not see policy filters and cannot override them.")
            if p.synthetic:
                st.error("Model trained on SYNTHETIC history - demonstration only, not an estimate for real decisions.")
            if p.policy_changed_since_training:
                st.warning(f"Policy changed since training ({p.policy_version_at_training}); retrain the model.")
            st.caption(f"Model {p.model_id} - {p.n_train} training cases - out-of-time AUC {p.test_auc:.3f} - "
                       f"calibration error {p.test_ece:.3f}. Does not override the rule outcome above.")
            for c in p.reason_codes:
                st.markdown(f"- {c}")


def page_assessment():
    r = need_applicant()
    if r is None:
        return
    hero("Assessment dashboard", f"{cfg.meta.display_name} · policy {cfg.meta.policy_version}", r)
    banner(r)
    decision_snapshot(r)
    st.markdown("**Underwriting pipeline**")
    pipeline_strip(r)
    st.write("")
    probability_panel(r)
    legend = " ".join(pill(t, c) for c, t in IND_TEXT.items())
    st.markdown(legend, unsafe_allow_html=True)
    st.write("")
    cols = st.columns(4)
    for i, key in enumerate(METRIC_ORDER):
        cols[i % 4].markdown(card(r.metrics[key]), unsafe_allow_html=True)

    st.subheader("Checks by underwriting stage")
    st.caption("Missing information gives REVIEW, never FAIL. DEVIATION = outside policy but within a band the named "
               "authority may approve. A FAIL cannot be offset by a high score, a preference or the model.")
    st.markdown(checks_table(r), unsafe_allow_html=True)

    with st.expander("Same applicant under every fictional bank configuration"):
        rows = []
        for b, c in BANKS.items():
            x = assess(r.applicant, c, timestamp=r.timestamp)
            rows.append({"Bank": c.meta.display_name, "Policy": c.meta.policy_version,
                         "Classification": x.classification.value,
                         "Score": None if x.readiness_score is None else round(x.readiness_score, 1),
                         "FAIL": sum(ch.status == CheckStatus.FAIL for ch in x.checks),
                         "DEVIATION": sum(ch.status == CheckStatus.DEVIATION for ch in x.checks),
                         "REVIEW": sum(ch.status == CheckStatus.REVIEW for ch in x.checks),
                         "Model probability": "-" if x.probability is None else f"{x.probability.probability:.0%}",
                         "Indicative range": "-" if x.capacity is None else
                         f"{inr(x.capacity.lower)} - {inr(x.capacity.upper)}"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Page 3: Explanation and diagnosis
# --------------------------------------------------------------------------
def page_explain():
    r = need_applicant()
    if r is None:
        return
    hero("Explanation & diagnosis", "Why the classification came out this way, factor by factor", r)
    banner(r)
    st.markdown("**Why this classification**")
    for reason in r.classification_reasons:
        st.markdown(f"- {reason}")

    groups = [("rule_failure", "Confirmed rule failures", "red"),
              ("deviation", "Deviations needing approval", "purple"),
              ("review", "Requires manual review", "amber"),
              ("missing_info", "Missing information", "grey"),
              ("risk_indicator", "Estimated risk indicators (potential factors)", "amber"),
              ("preference", "Bank preference adjustments", "info"),
              ("met", "Criteria met", "green")]
    c1, c2 = st.columns(2)
    for i, (kind, title, color) in enumerate(groups):
        items = [f for f in r.findings if f.kind == kind]
        with (c1 if i % 2 == 0 else c2):
            st.markdown(f"#### {pill(str(len(items)), color)} {title}", unsafe_allow_html=True)
            if not items:
                st.caption("None")
            for f in items:
                st.markdown(f"- {html.escape(f.text)}  \n  <span style='font-size:.72rem;opacity:.6'>"
                            f"{f.evidence.value}</span>", unsafe_allow_html=True)

    st.subheader("Which factors affect the readiness score most")
    st.caption("Points lost = effective weight x (100 - sub-score). Missing factors use a conservative placeholder; "
               "not-applicable factors are excluded.")
    df = pd.DataFrame([{"Factor": f.label, "Status": f.status.value,
                        "Sub-score": None if f.score is None else round(f.score, 1),
                        "Configured weight": f.weight, "Effective weight": round(f.effective_weight, 3),
                        "Contribution": round(f.contribution, 2), "Points lost": round(f.points_lost, 2),
                        "Input": f.raw_input} for f in r.factors])
    chart = alt.Chart(df[df["Status"] != FactorStatus.NOT_APPLICABLE.value]).mark_bar(color=COLORS["amber"]).encode(
        x=alt.X("Points lost:Q", title="Points lost from 100"),
        y=alt.Y("Factor:N", sort="-x", title=None),
        tooltip=["Factor", "Status", "Sub-score", "Effective weight", "Points lost"]).properties(height=260)
    st.altair_chart(chart, width="stretch")
    st.dataframe(df, hide_index=True, width="stretch")
    if r.score_adjustments:
        st.caption(f"Base score {r.base_readiness_score:.1f} + preferences "
                   f"{sum(a.points for a in r.score_adjustments):+g} (capped at +/-{cfg.score_adjustment_cap:g}) "
                   f"= {r.readiness_score:.1f}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Income adjustment steps**")
        for s in r.income.steps:
            st.markdown(f"- {s}")
        st_ = r.stress
        st.markdown("**Stress and income-volatility model**")
        st.markdown(
            f"- Stressed EMI {inr(st_.stressed_emi)} (rate shock {st_.rate_shock_applied_bps:g} bps), stressed "
            f"income {inr(st_.stressed_income)}, stressed FOIR "
            f"{'-' if st_.stressed_foir is None else f'{st_.stressed_foir:.1%}'}\n"
            f"- Income CV {'-' if st_.cv_used is None else f'{st_.cv_used:.2f}'} ({st_.cv_source}); income needed to "
            f"keep FOIR within cap {inr(st_.shortfall_threshold)}\n"
            f"- Monthly P(FOIR > cap) "
            f"{'-' if st_.shortfall_probability is None else f'{st_.shortfall_probability:.1%}'}; expected months "
            f"per year above cap {'-' if st_.expected_shortfall_months_per_year is None else f'{st_.expected_shortfall_months_per_year:.1f}'}\n"
            f"- EMI rises about {inr(st_.emi_rate_sensitivity)} per +1 percentage point of interest\n"
            f"- APR {r.apr.apr_effective:.2f}% effective ({r.apr.apr_nominal:.2f}% nominal) after "
            f"{inr(r.apr.upfront_charges)} upfront charges")
    with c2:
        st.markdown("**Data completeness** (reliability of the assessment, not a credit criterion)")
        st.dataframe(pd.DataFrame([{"Item": i.label, "Required": "Yes" if i.required else "Optional",
                                    "Status": i.status.replace("_", " ")} for i in r.completeness.items]),
                     hide_index=True, width="stretch")
        st.markdown(f"**KYC:** PAN {r.kyc['pan_masked']}"
                    + (f" - holder type {r.kyc['holder_type']} ({r.kyc['holder_type_label']})" if r.kyc['holder_type']
                       else "") + f" - status {r.kyc['status'].replace('_', ' ')}")

    st.subheader("Suggested next steps for the bank manager")
    for i, s in enumerate(r.next_steps, 1):
        st.markdown(f"{i}. {s}")


# --------------------------------------------------------------------------
# Page 4: Loan simulator
# --------------------------------------------------------------------------
def page_simulator():
    base = saved_applicant()
    r0 = need_applicant()
    if r0 is None:
        return
    hero("Loan simulator", "Try other amounts, tenures, rates and EMIs without changing the customer record", r0)
    st.warning("Hypothetical scenario only. Changing these values does **not** change the customer's recorded "
               "financial profile, and nothing here is saved to the record.")
    base_income = base.verified_monthly_income if base.verified_monthly_income is not None else base.net_monthly_income
    sig = f"{bank_id}|{json.dumps(base.to_state(), sort_keys=True)}"
    if ss.get("sim_for") != sig:
        ss.update(sim_for=sig, sim_amount=float(base.requested_amount), sim_tenure=int(base.tenure_months),
                  sim_rate=float(base.annual_interest_rate),
                  sim_emis=float(base.existing_monthly_emis or 0.0),
                  sim_income=float(base_income) if base_income is not None else None,
                  sim_ratetype=base.rate_type)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.number_input("Loan amount (INR)", min_value=1.0, step=25_000.0, format="%.0f", key="sim_amount")
    c2.number_input("Tenure (months)", min_value=1, max_value=480, step=6, key="sim_tenure")
    c3.number_input("Interest rate (% p.a.)", min_value=0.0, max_value=60.0, step=0.25, key="sim_rate")
    c4.selectbox("Rate type", ["fixed", "floating"], key="sim_ratetype")
    c5.number_input("Existing EMIs (INR)", min_value=0.0, step=1_000.0, format="%.0f", key="sim_emis")
    c6.number_input("Monthly income (INR)", min_value=0.0, step=1_000.0, format="%.0f", key="sim_income",
                    help="Replaces declared income (and verified income, if one was recorded).")
    if ss.sim_income is None:
        st.info("Enter an income assumption to simulate; the recorded profile has no income.")
        return

    data = base.to_state()
    data.update(requested_amount=ss.sim_amount, tenure_months=int(ss.sim_tenure), annual_interest_rate=ss.sim_rate,
                existing_monthly_emis=ss.sim_emis, net_monthly_income=ss.sim_income, rate_type=ss.sim_ratetype)
    if base.verified_monthly_income is not None:
        data["verified_monthly_income"] = ss.sim_income
    sim = ApplicantInput.model_validate(data)
    r1 = assess(sim, cfg, timestamp=r0.timestamp)

    def row(label, f):
        return {"Metric": label, "Recorded profile": f(r0), "Hypothetical scenario": f(r1)}

    def pct(x):
        return "-" if x is None else f"{x:.1%}"

    def counts(r):
        return " / ".join(str(sum(c.status == s for c in r.checks))
                          for s in (CheckStatus.FAIL, CheckStatus.DEVIATION, CheckStatus.REVIEW))
    rows = [
        row("Loan amount", lambda r: inr(r.applicant.requested_amount)),
        row("Tenure / rate", lambda r: f"{r.applicant.tenure_months} m @ {r.applicant.annual_interest_rate:g}% "
                                       f"{r.applicant.rate_type}"),
        row("Adjusted monthly income", lambda r: inr(r.income.adjusted_income)),
        row("Proposed EMI", lambda r: inr(r.emi.emi)),
        row("APR (effective)", lambda r: f"{r.apr.apr_effective:.2f}%"),
        row("FOIR", lambda r: pct(r.metrics["foir"].value)),
        row("Stressed FOIR", lambda r: pct(r.stress.stressed_foir)),
        row("Monthly P(FOIR > cap)", lambda r: pct(r.stress.shortfall_probability)),
        row("Residual income", lambda r: inr(r.metrics["residual_income"].value)),
        row("Indicative capacity", lambda r: r.metrics["indicative_max_loan"].display),
        row("FAIL / DEVIATION / REVIEW", counts),
        row("Readiness score", lambda r: r.metrics["readiness_score"].display),
        row("Model approval probability", lambda r: "-" if r.probability is None else f"{r.probability.probability:.0%}"),
        row("Classification", lambda r: r.classification.value),
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    banner(r1)

    st.subheader("How EMI, FOIR and indicative capacity change with tenure (scenario)")
    income = calc.adjust_income(sim, cfg.income_adjustment)
    cap = calc.effective_tenure_cap(sim.age, cfg.eligibility.max_age_at_maturity, int(cfg.eligibility.tenure_months.max))
    max_t = int(cfg.eligibility.tenure_months.max) if cap is None else max(6, min(int(cfg.eligibility.tenure_months.max), cap))
    pts = []
    for n in sorted(set(list(range(6, max_t + 1, 6)) + [max_t])):
        e = calc.emi(sim.requested_amount, sim.annual_interest_rate, n)
        f = calc.foir(sim.existing_monthly_emis, e, income.adjusted_income) if income.adjusted_income else None
        c = compute_capacity(sim.model_copy(update={"tenure_months": n}), cfg, income, cap)
        pts.append({"Tenure (months)": n, "EMI": e, "FOIR": f,
                    "Capacity (lower)": c.lower if c else None, "Capacity (upper)": c.upper if c else None})
    df = pd.DataFrame(pts)
    c1, c2 = st.columns(2)
    with c1:
        long = df.melt("Tenure (months)", ["Capacity (lower)", "Capacity (upper)"], "Series", "INR")
        line = alt.Chart(long).mark_line(point=True).encode(
            x="Tenure (months):Q", y=alt.Y("INR:Q", title="Indicative capacity (INR)"), color="Series:N")
        req = alt.Chart(pd.DataFrame({"y": [sim.requested_amount]})).mark_rule(strokeDash=[6, 4], color="#888").encode(
            y=alt.Y("y:Q", title="Indicative capacity (INR)"))
        st.altair_chart((line + req).properties(height=300, title="Capacity vs tenure (dashed = requested)"),
                        width="stretch")
    with c2:
        if sim.employment_category is not None and df["FOIR"].notna().any():
            fcap = cfg.eligibility.foir_cap.get(sim.employment_category)
            line = alt.Chart(df).mark_line(point=True, color=COLORS["info"]).encode(
                x="Tenure (months):Q", y=alt.Y("FOIR:Q", title="FOIR", axis=alt.Axis(format="%")),
                tooltip=["Tenure (months)", alt.Tooltip("EMI:Q", format=",.0f"), alt.Tooltip("FOIR:Q", format=".1%")])
            rule = alt.Chart(pd.DataFrame({"y": [fcap]})).mark_rule(color=COLORS["red"], strokeDash=[6, 4]).encode(
                y=alt.Y("y:Q", title="FOIR"))
            st.altair_chart((line + rule).properties(height=300, title=f"FOIR vs tenure (red = cap {fcap:.0%})"),
                            width="stretch")

    st.subheader("Amortisation schedule (scenario)")
    sched = pd.DataFrame(calc.amortisation_schedule(sim.requested_amount, sim.annual_interest_rate,
                                                    sim.tenure_months))
    long = sched.melt("month", ["interest", "principal"], "Component", "INR")
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(long).mark_area().encode(
        x=alt.X("month:Q", title="Month"), y=alt.Y("INR:Q", stack=True, title="EMI split (INR)"),
        color="Component:N").properties(height=260, title="Interest vs principal in each EMI"), width="stretch")
    c2.altair_chart(alt.Chart(sched).mark_line(color=COLORS["info"]).encode(
        x=alt.X("month:Q", title="Month"), y=alt.Y("balance:Q", title="Outstanding (INR)")).properties(
        height=260, title="Outstanding balance"), width="stretch")
    if r1.capacity:
        st.markdown("**Rate sensitivity of indicative capacity (scenario)**")
        st.dataframe(pd.DataFrame([{"Scenario": s["scenario"], "Rate": f"{s['rate']:g}%", "Lower": inr(s["lower"]),
                                    "Upper": inr(s["upper"])} for s in r1.capacity.rate_sensitivity]),
                     hide_index=True, width="stretch")
    st.caption("Indicative capacity is a rule-based estimate, not a lender-approved limit.")


# --------------------------------------------------------------------------
# Page 5: Report
# --------------------------------------------------------------------------
def page_report():
    r = need_applicant()
    if r is None:
        return
    hero("Reports & customer letter", "Manager screening report, structured data, and a plain-language letter "
                                      "for the customer", r)
    d = current_advice(r)
    pricing = recommend_rate(r, cfg)
    md = report_markdown(r, d, pricing)
    letter = customer_letter(r, d)
    fn = f"APEX_{r.applicant.applicant_id}_{r.bank_id}_{r.timestamp[:10]}"
    c1, c2, c3, _ = st.columns([1, 1, 1, 2])
    c1.download_button("Manager report (.md)", md, file_name=f"{fn}.md", mime="text/markdown", width="stretch")
    c2.download_button("Customer letter (.md)", letter, file_name=f"{fn}_customer_letter.md", mime="text/markdown",
                       width="stretch", type="primary")
    c3.download_button("Data (.json)", report_json(r, pricing), file_name=f"{fn}.json", mime="application/json",
                       width="stretch")
    t1, t2 = st.tabs(["Customer letter", "Manager screening report"])
    with t1:
        st.caption("Shareable with the customer: no PAN, internal scores or model outputs. Review before sending.")
        with st.container(border=True):
            st.markdown(letter)
    with t2:
        with st.container(border=True):
            st.markdown(md)


# --------------------------------------------------------------------------
# Decision & advice: how much, why, why not, options, where to improve
# --------------------------------------------------------------------------
REASON_STYLE = {"strength": ("green", "✓"), "blocker": ("red", "✕"), "concern": ("amber", "!"),
                "missing": ("grey", "?")}


def decision_snapshot(r: Assessment) -> None:
    d = current_advice(r)
    rate = recommend_rate(r, cfg)
    c = COLORS[d.tone]
    rate_line = (f"Illustrative policy rate: {rate.recommended_rate_pct:g}% p.a. (not a quote). "
                 if rate.status == "available" else "No illustrative rate yet. ")
    st.markdown(
        f'<div class="ax-banner" style="border-left:8px solid {c}">'
        f'<div style="font-size:.78rem;color:#64748b">Decision support · conditional affordability</div>'
        f'<div style="font-size:1.1rem;font-weight:750;color:{c}">{html.escape(d.verdict)}</div>'
        f'<div style="font-size:.84rem">{html.escape(d.detail)} {html.escape(rate_line)}'
        f'Open <b>Decision &amp; advice</b> for why, why not, '
        f'options and an improvement plan.</div></div>', unsafe_allow_html=True)


def reason_html(x) -> str:
    color, icon = REASON_STYLE[x.kind]
    return (f'<div class="ax-reason" style="border-left-color:{COLORS[color]};background:{TINT[color]}">'
            f'<b>{icon} {html.escape(x.title)}</b>{html.escape(x.text)}</div>')


def option_html(o) -> str:
    color = CLASS_COLOR[o.classification]
    badge = '<span class="ax-pill" style="background:#15803d;float:right">Recommended</span>' if o.recommended else ""
    rows = [("Amount", inr(o.amount)), ("Tenure", f"{o.tenure_months} months"), ("EMI", inr(o.emi)),
            ("Total interest", inr(o.total_interest)), ("FOIR", "-" if o.foir is None else f"{o.foir:.1%}"),
            ("Outcome", o.classification.value.split(" ")[0] + (" · exception" if o.deviations else "")
             + (" · fails rules" if o.fails else ""))]
    body = "".join(f"<tr><td>{k}</td><td>{html.escape(v)}</td></tr>" for k, v in rows)
    return (f'<div class="ax-option{" rec" if o.recommended else ""}" style="border-top:4px solid {COLORS[color]}">'
            f'{badge}<div class="h">{html.escape(o.title)}</div><div class="d">{html.escape(o.description)}</div>'
            f'<table>{body}</table></div>')


def step_html(i, n: int) -> str:
    chips = []
    if i.quantified:
        if i.score_before is not None and i.score_after is not None:
            ds = i.score_after - i.score_before
            chips.append((f"Score {i.score_before:.0f} → {i.score_after:.0f}", ds >= 0.5))
        if i.amount_after is not None and i.amount_before is not None and i.amount_after != i.amount_before:
            chips.append((f"Max amount {inr(i.amount_before)} → {inr(i.amount_after)}", i.amount_after > i.amount_before))
        elif i.amount_after is not None and i.amount_before is None:
            chips.append((f"Max amount → {inr(i.amount_after)}", True))
        if i.classification_after is not None and i.classification_after != i.classification_before:
            chips.append((f"{i.classification_before.value.split(' ')[0]} → {i.classification_after.value.split(' ')[0]}",
                          True))
        if not chips or all(not good for _, good in chips):
            chips.append(("Small effect on this assessment", False))
    else:
        chips.append((i.note or "Not quantified", False))
    chip_html = "".join(f'<span class="ax-delta{"" if good else " flat"}">{html.escape(t)}</span>' for t, good in chips)
    title = i.area if i.key == "combined" else f"{i.area}: {i.current} → {i.target}"
    return (f'<div class="ax-step"><div class="ax-num">{"Σ" if i.key == "combined" else n}</div>'
            f'<div style="flex:1"><div class="a">{html.escape(title)}</div>'
            f'<div>{html.escape(i.action)}</div><div class="m">Time frame: {html.escape(i.horizon)}</div>'
            f'{chip_html}</div></div>')


def page_decision():
    r = need_applicant()
    if r is None:
        return
    hero("Decision & advice", "What the current assumptions support, why, and how the customer can improve", r)
    d = current_advice(r)
    c = COLORS[d.tone]
    st.markdown(f'<div class="ax-banner" style="border-left:8px solid {c};background:{TINT[d.tone]}">'
                f'<div style="font-size:1.35rem;font-weight:800;color:{c}">{html.escape(d.verdict)}</div>'
                f'<div style="font-size:.9rem;margin-top:2px">{html.escape(d.detail)}</div></div>',
                unsafe_allow_html=True)

    k = st.columns(5)
    k[0].markdown(kpi("Requested", inr(d.requested_amount), f"{d.requested_tenure} months"), unsafe_allow_html=True)
    k[1].markdown(kpi("Affordability ceiling", inr(d.max_eligible_amount), f"conditional · {d.tenure_used or '-'} months",
                      "green" if d.max_eligible_amount and d.max_eligible_amount >= d.requested_amount else "amber"),
                  unsafe_allow_html=True)
    k[2].markdown(kpi("Indicative amount", inr(d.recommended_amount),
                      "Not available" if d.recommended_amount is None else f"EMI {inr(d.recommended_emi)}",
                      d.tone), unsafe_allow_html=True)
    k[3].markdown(kpi("Max at longest tenure", inr(d.max_amount_any_tenure), f"{d.max_tenure or '-'} months"),
                  unsafe_allow_html=True)
    k[4].markdown(kpi("Limiting factor", d.binding_constraint or "-", "lowest of all limits", small=True),
                  unsafe_allow_html=True)
    if d.max_eligible_amount:
        if d.requested_amount <= d.max_eligible_amount:
            frac = d.requested_amount / d.max_eligible_amount
            st.progress(frac, text=f"The request uses {frac:.0%} of the conditional affordability ceiling")
        else:
            frac = d.max_eligible_amount / d.requested_amount
            st.progress(frac, text=f"{frac:.0%} of the request is supported by affordability")
    if d.blockers:
        st.error("**Must be resolved before any offer:**\n\n" + "\n".join(f"- {b}" for b in d.blockers))
    st.caption("The affordability ceiling assumes the entered interest rate and income are verified. "
               "It is not an approval or a bank offer.")

    pricing = recommend_rate(r, cfg)
    st.subheader("Illustrative interest-rate recommendation")
    if pricing.status == "available":
        st.metric("Suggested annual rate", f"{pricing.recommended_rate_pct:g}%",
                  help="A fictional policy calculation, not an AI prediction or lender quote.")
        st.caption(f"Example base {pricing.base_rate_pct:g}% + the adjustments below = "
                   f"{pricing.unclamped_rate_pct:g}%; bounded to this bank's example range "
                   f"{pricing.range_low_pct:g}%–{pricing.range_high_pct:g}%.")
        for factor in pricing.factors:
            st.markdown(f"- **{factor.label}: {factor.adjustment_pp:+g} percentage points.** "
                        f"{factor.explanation}")
        st.caption("This rate is conditional on the fictional policy and entered data. It may still require "
                   "a smaller loan or manual review. Applying it recalculates EMI, affordability, and APR.")
        if abs(r.applicant.annual_interest_rate - pricing.recommended_rate_pct) > 1e-9:
            st.button("Apply illustrative rate and reassess", on_click=apply_illustrative_rate,
                      args=(pricing.recommended_rate_pct,))
        else:
            st.caption("The current assessment already uses this illustrative rate.")
    else:
        st.info("No illustrative rate is available yet. " + " ".join(pricing.reasons))

    t1, t2, t3, t4, t5 = st.tabs(["How much & why", "Why / why not", "Options", "Where to improve", "Customer letter"])
    with t1:
        if not d.limits:
            st.info("Limits need usable income, existing EMIs and employment category.")
        else:
            st.markdown("Each limit caps the loan independently; **the lowest one sets the amount.**")
            df = pd.DataFrame([{"Limit": x.label, "Maximum amount": x.max_amount, "Binding": x.binding,
                                "Explanation": x.customer_text, "Formula": x.formula} for x in d.limits])
            top = max(df["Maximum amount"].max(), d.requested_amount) * 1.08
            bars = alt.Chart(df).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("Maximum amount:Q", title="Maximum loan under this limit (INR)", scale=alt.Scale(domain=[0, top])),
                y=alt.Y("Limit:N", sort="x", title=None, axis=alt.Axis(labelLimit=260)),
                color=alt.condition("datum.Binding", alt.value(COLORS["amber"]), alt.value("#93c5fd")),
                tooltip=["Limit", alt.Tooltip("Maximum amount:Q", format=",.0f"), "Explanation"])
            rule = alt.Chart(pd.DataFrame({"x": [d.requested_amount], "t": ["Requested"]})).mark_rule(
                color=COLORS["red"], strokeDash=[6, 4], size=2).encode(x="x:Q")
            st.altair_chart((bars + rule).properties(height=46 * len(df) + 30,
                                                     title="Affordability limits (orange = binding, red line = requested)"),
                            width="stretch")
            st.markdown(html_table(["Limit", "Max amount", "", "What it means", "Formula"],
                                   [[html.escape(x.label), inr(x.max_amount), pill("binding", "amber") if x.binding else "",
                                     html.escape(x.customer_text), f"<code>{html.escape(x.formula)}</code>"]
                                    for x in d.limits]), unsafe_allow_html=True)
            c1, c2 = st.columns([3, 2])
            with c1:
                fr = pd.DataFrame(d.frontier)
                line = alt.Chart(fr).mark_line(point=True, color=COLORS["info"]).encode(
                    x=alt.X("tenure:Q", title="Tenure (months)"), y=alt.Y("max_amount:Q", title="Maximum amount (INR)"),
                    tooltip=[alt.Tooltip("tenure:Q"), alt.Tooltip("max_amount:Q", format=",.0f"), "binding"])
                pt = alt.Chart(pd.DataFrame({"tenure": [d.requested_tenure], "amt": [d.requested_amount]})).mark_point(
                    shape="diamond", size=160, filled=True, color=COLORS["red"]).encode(x="tenure:Q", y="amt:Q")
                st.altair_chart((line + pt).properties(height=290, title="Affordability frontier: maximum amount by "
                                                                         "tenure (red = request)"), width="stretch")
            with c2:
                st.markdown("**What moves the maximum amount**")
                for s in d.sensitivities:
                    st.markdown(kpi(s.label, ("+" if s.effect >= 0 else "−") + inr(abs(s.effect)), "change in affordability ceiling"),
                                unsafe_allow_html=True)
                    st.write("")
                if d.required_income is not None or d.max_existing_emis is not None:
                    st.caption(f"Full request fits at usable income {inr(d.required_income)} (now "
                               f"{inr(r.income.adjusted_income)}) or existing EMIs of at most {inr(d.max_existing_emis)} "
                               f"(now {inr(r.applicant.existing_monthly_emis)}).")
    with t2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### ✅ Why - what works in the customer's favour")
            st.markdown("".join(reason_html(x) for x in d.why) or "<i>No clear strengths identified yet.</i>",
                        unsafe_allow_html=True)
        with c2:
            st.markdown("#### ⚠️ Why not - what holds it back")
            st.markdown("".join(reason_html(x) for x in d.why_not) or "<i>Nothing material.</i>",
                        unsafe_allow_html=True)
    with t3:
        st.caption("Every option is re-assessed by the full engine (rules, deviations, stress test, score).")
        for i in range(0, len(d.options), 3):
            cols = st.columns(3)
            for col, o in zip(cols, d.options[i:i + 3]):
                col.markdown(option_html(o), unsafe_allow_html=True)
            st.write("")
    with t4:
        st.caption("Each step is applied to a copy of the application and the assessment is re-run, so the effects "
                   "are measured, not assumed. Ordered by impact. Bureaus and lenders, not APEX, decide actual "
                   "score changes and approvals.")
        if not d.improvements:
            st.success("No specific improvements identified - the application is already strong on every factor.")
        st.markdown("".join(step_html(i, n) for n, i in enumerate(d.improvements, 1)), unsafe_allow_html=True)
    with t5:
        letter = customer_letter(r, d)
        st.download_button("Download customer letter (.md)", letter, type="primary",
                           file_name=f"APEX_{r.applicant.applicant_id}_customer_letter.md", mime="text/markdown")
        with st.container(border=True):
            st.markdown(letter)


# --------------------------------------------------------------------------
# Guide
# --------------------------------------------------------------------------
def page_guide():
    hero("Guide", "How to use APEX, what it does, and the mathematics behind it")
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        with c1:
            st.markdown("#### 📦 Install APEX")
            download_zip_button(st, "dl_guide", primary=True)
            st.caption(f"{len(bundle.project_files())} files: source, bank configs, docs, tests, installers.")
        with c2:
            st.markdown(
                "1. Download the zip and **extract** it (right-click → *Extract All* on Windows).\n"
                "2. Install **Python 3.10+** from python.org if needed (tick *Add Python to PATH*).\n"
                "3. Open the extracted `APEX` folder and run the installer:\n"
                "   - **Windows:** double-click `install_and_run.bat`\n"
                "   - **macOS / Linux:** `bash install_and_run.sh`\n"
                "4. The installer creates a virtual environment, installs the dependencies and opens "
                "APEX at http://localhost:8501. Run it again any time to start APEX.")
    path =Path(__file__).resolve().parent / "docs" / "APEX_HANDBOOK.md"
    if not path.exists():
        st.info("docs/APEX_HANDBOOK.md not found.")
        return
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^# (?=Part )", text)
    tabs = st.tabs(["Overview"] + [p.split("\n", 1)[0].strip() for p in parts[1:]])
    with tabs[0]:
        st.markdown(parts[0])
    for tab, p in zip(tabs[1:], parts[1:]):
        with tab:
            st.markdown("# " + p)


# --------------------------------------------------------------------------
# Page 6: Policy studio
# --------------------------------------------------------------------------
def _rule_rows(rules):
    return [{"On": "Yes" if r.enabled else "No", "ID": r.id, "Stage": r.stage, "Rule": r.label,
             "Requirement / condition": requirement_text(r), "Action": r.action,
             "If breached": (f"FAIL" if r.action == "hard" else f"DEVIATION - {r.authority}" if r.action == "deviation"
                             else "REVIEW" if r.action == "review" else f"{r.points:+g} points when true"),
             "Missing data": r.on_missing, "Applies to": "all" if not r.applies_to else
             ", ".join(c.value for c in r.applies_to), "Author": r.author} for r in rules]


def _update_custom(new_rules, action, rule_id):
    try:
        save_overlay(CONFIG_DIR, bank_id, new_rules, action, rule_id, ss.get("pr_author") or "unknown")
        ss.pr_msg = ("success", f"Rule {rule_id}: {action} saved. Policy version updated.")
    except RuleError as e:
        ss.pr_msg = ("error", str(e))


def toggle_rule(rule_id):
    rules = [r.model_copy(update={"enabled": not r.enabled}) if r.id == rule_id else r for r in cfg.custom_rules]
    _update_custom(rules, "toggle", rule_id)


def delete_rule(rule_id):
    _update_custom([r for r in cfg.custom_rules if r.id != rule_id], "delete", rule_id)


def build_rule_from_widgets() -> PolicyRule:
    field = ss.pr_field if ss.pr_field != "__other__" else (ss.get("pr_other_field") or "").strip()
    spec = check_field_allowed(field)   # raises FairnessError / RuleError
    op = ss.pr_op
    if spec.kind == "number":
        value = float(ss.pr_num)
    elif spec.kind == "category":
        value = list(ss.pr_cats) if op in ("in", "not_in") else (ss.pr_cats[0] if ss.pr_cats else None)
    else:
        value = None
    rule = PolicyRule(
        id=ss.pr_id.strip(), label=ss.pr_label.strip(), stage=ss.pr_stage, field=field, operator=op, value=value,
        applies_to=list(ss.pr_applies) or None, action=ss.pr_action,
        points=float(ss.pr_points) if ss.pr_action == "score" else 0,
        authority=(ss.pr_authority.strip() or None) if ss.pr_action == "deviation" else None,
        on_missing=ss.pr_missing, rationale=ss.pr_rationale.strip(), author=ss.pr_author.strip() or "unknown",
        created=date.today())
    if rule.id in {r.id for r in cfg.policy_rules + cfg.custom_rules}:
        raise RuleError(f"A rule with id '{rule.id}' already exists.")
    validate_rule(rule)
    return rule


def portfolio_impact(base_cfg, new_cfg):
    rows = []
    for p in PROFILES:
        a = assess(p.applicant, base_cfg, timestamp="impact", use_model=False).classification
        b = assess(p.applicant, new_cfg, timestamp="impact", use_model=False).classification
        rows.append({"Profile": f"{p.key} - {p.title}", "Before": a.value, "After": b.value, "Changed": a != b})
    return pd.DataFrame(rows)


def page_policy():
    hero("Policy studio", "Bank-specific filters, deviations and preferences - versioned, audited, fairness-guarded")
    st.caption(f"{cfg.meta.display_name} - policy {cfg.meta.policy_version}, last verified {cfg.meta.last_verified}. "
               "Managers add their own filters, deviation rules and preferences here; every change is versioned and "
               "audited. Rules on protected characteristics or their proxies are blocked.")
    if msg := ss.pop("pr_msg", None):
        (st.success if msg[0] == "success" else st.error)(msg[1])

    tab1, tab2, tab3, tab4 = st.tabs(["Current policy", "Add a rule", "Deviation matrix", "Audit log"])
    with tab1:
        e = cfg.eligibility
        st.markdown("**Core thresholds** (from the bank configuration file)")
        st.dataframe(pd.DataFrame([
            {"Parameter": "FOIR cap (salaried / self-employed / informal)",
             "Value": f"{e.foir_cap.salaried:.0%} / {e.foir_cap.self_employed:.0%} / {e.foir_cap.informal:.0%}"},
            {"Parameter": "Minimum credit score / new-to-credit", "Value": f"{e.credit_score.min_score} / "
                                                                          f"{e.credit_score.new_to_credit}"},
            {"Parameter": "Minimum net income", "Value": inr(e.min_net_monthly_income)},
            {"Parameter": "Age limits", "Value": f"{e.min_age} - {e.max_age_at_maturity} at maturity"},
            {"Parameter": "Loan amount / tenure", "Value": f"{inr(e.loan_amount.min)} - {inr(e.loan_amount.max)} / "
                                                           f"{e.tenure_months.min:g}-{e.tenure_months.max:g} months"},
            {"Parameter": "Accepted categories", "Value": ", ".join(c.value for c in e.accepted_employment_categories)},
            {"Parameter": "Income multiple / safety buffer", "Value": f"{cfg.amount.income_multiple:g}x / "
                                                                      f"{cfg.amount.safety_buffer_min:.0%}-{cfg.amount.safety_buffer_max:.0%}"},
            {"Parameter": "Stress test", "Value": f"income -{cfg.stress.income_shock_pct:.0%}, rate +"
                                                  f"{cfg.stress.rate_shock_bps:g} bps (floating), max P(FOIR>cap) "
                                                  f"{cfg.stress.max_shortfall_probability:.0%}"},
            {"Parameter": "KYC", "Value": f"PAN holder types {', '.join(cfg.kyc.allowed_holder_types)}; verified "
                                          f"required: {cfg.kyc.require_verified}"},
            {"Parameter": "Preference cap", "Value": f"+/-{cfg.score_adjustment_cap:g} points"},
        ]), hide_index=True, width="stretch")
        st.markdown("**Bank policy rules** (configuration file)")
        st.dataframe(pd.DataFrame(_rule_rows(cfg.policy_rules)), hide_index=True, width="stretch")
        st.markdown("**Custom rules** (added in this studio)")
        if not cfg.custom_rules:
            st.caption("None yet - add one in the next tab.")
        for r in cfg.custom_rules:
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.markdown(f"{'🟢' if r.enabled else '⚪'} **{r.id}** - {r.label}: {requirement_text(r)} "
                        f"→ *{r.action}*" + (f" ({r.points:+g} pts)" if r.action == "score" else "")
                        + f" - by {r.author}")
            c2.button("Disable" if r.enabled else "Enable", key=f"tg_{r.id}", on_click=toggle_rule, args=(r.id,))
            c3.button("Delete", key=f"del_{r.id}", on_click=delete_rule, args=(r.id,))

    with tab2:
        st.markdown("Describe the requirement the applicant must meet. Hard / deviation / review rules act when the "
                    "requirement is **not** met; score rules add points when the condition **is** true.")
        for k, v in dict(pr_field="max_dpd_12m", pr_stage="Bureau", pr_action="hard", pr_missing="review").items():
            ss.setdefault(k, v)
        c1, c2, c3 = st.columns(3)
        c1.text_input("Rule ID", key="pr_id", placeholder="e.g. CUS-ENQ6")
        c2.text_input("Label", key="pr_label", placeholder="e.g. At most 5 enquiries in 6 months")
        c3.text_input("Your name (audit trail)", key="pr_author")
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Stage", list(STAGES), key="pr_stage")
        c2.selectbox("Field", list(FIELD_REGISTRY) + ["__other__"], key="pr_field",
                     format_func=lambda f: "Other (type a field name)..." if f == "__other__"
                     else f"{FIELD_REGISTRY[f].label} [{f}]")
        if ss.pr_field == "__other__":
            c3.text_input("Field name", key="pr_other_field",
                          help="Only whitelisted fields are allowed; protected attributes are refused.")
            spec = FIELD_REGISTRY.get((ss.get("pr_other_field") or "").strip())
        else:
            spec = FIELD_REGISTRY[ss.pr_field]
        if spec is not None:
            if spec.fairness_note:
                st.warning(f"Fairness note: {spec.fairness_note}")
            ops = sorted(OPERATORS_BY_KIND[spec.kind])
            if ss.get("pr_op") not in ops:
                ss.pr_op = ops[0]
            c1, c2, c3 = st.columns(3)
            c1.selectbox("Operator", ops, key="pr_op", format_func=lambda o: f"{o}  ({OP_TEXT[o]})")
            if spec.kind == "number":
                ss.setdefault("pr_num", 0.0)
                c2.number_input(f"Value ({spec.unit or 'number'})", key="pr_num", format="%.4g",
                                help="Fractions for ratios: 0.45 = 45%.")
            elif spec.kind == "category":
                ss.setdefault("pr_cats", [])
                c2.multiselect("Value(s)", list(spec.options), key="pr_cats")
            c3.multiselect("Applies to categories (empty = all)", [c.value for c in EmploymentCategory],
                           key="pr_applies")
        c1, c2, c3, c4 = st.columns(4)
        c1.selectbox("Action", ["hard", "deviation", "review", "score"], key="pr_action",
                     format_func=lambda a: {"hard": "Hard filter (FAIL)", "deviation": "Deviation (needs approval)",
                                            "review": "Manual review", "score": "Preference (score points)"}[a])
        if ss.pr_action == "score":
            ss.setdefault("pr_points", 3.0)
            c2.number_input("Points (+/-)", min_value=-50.0, max_value=50.0, step=1.0, key="pr_points")
        if ss.pr_action == "deviation":
            ss.setdefault("pr_authority", "Branch Credit Manager")
            c2.text_input("Approving authority", key="pr_authority")
        c3.selectbox("If data is missing", ["review", "ignore"], key="pr_missing")
        st.text_area("Rationale (shown to managers and in the audit log)", key="pr_rationale", height=68)

        b1, b2, _ = st.columns([1, 1, 3])
        preview = b1.button("Preview impact", width="stretch")
        save = b2.button("Save rule", type="primary", width="stretch")
        if preview or save:
            try:
                rule = build_rule_from_widgets()
                new_cfg = cfg.model_copy(update={"custom_rules": cfg.custom_rules + [rule]})
                impact = portfolio_impact(cfg, new_cfg)
                changed = int(impact["Changed"].sum())
                st.info(f"Rule: {requirement_text(rule)} -> {rule.action}. Changes the classification of "
                        f"{changed} of {len(impact)} synthetic profiles.")
                st.dataframe(impact[impact["Changed"]] if changed else impact.head(0), hide_index=True,
                             width="stretch")
                for w in validate_rule(rule):
                    st.warning(w)
                if save:
                    _update_custom(cfg.custom_rules + [rule], "add", rule.id)
                    st.rerun()
            except (RuleError, ValidationError, ValueError) as e:
                st.error(str(e))

    with tab3:
        st.caption("A breach within a tier becomes DEVIATION (approvable by the named authority) instead of FAIL. "
                   "Edit the bank's YAML file to change tiers.")
        rows = [{"Check": k, "Breach up to": t.within, "Approver": t.authority}
                for k, tiers in cfg.deviation_matrix.items() for t in tiers]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch") if rows else st.caption("No deviation tiers.")

    with tab4:
        log = read_audit_log(CONFIG_DIR, bank_id)
        st.dataframe(pd.DataFrame(log[::-1]), hide_index=True, width="stretch") if log else st.caption("No changes yet.")


# --------------------------------------------------------------------------
# Page 7: Model lab
# --------------------------------------------------------------------------
def show_model(m: dict, title: str):
    mt = m["metrics"]
    st.markdown(f"#### {title}")
    if m["synthetic"]:
        st.error("Trained on SYNTHETIC history. Its probabilities only reproduce an invented decision process.")
    if m["gate"]["passed"]:
        st.success("Passed the bank's validation gate.")
    else:
        st.error("Failed the validation gate: " + "; ".join(m["gate"]["reasons"]) + ". It will not be used.")
    c = st.columns(6)
    c[0].metric("Test AUC (out-of-time)", f"{mt['test_auc']:.3f}", help="Probability an approved case outranks a "
                                                                        "declined one.")
    c[1].metric("Gini", f"{mt['test_gini']:.3f}")
    c[2].metric("KS", f"{mt['test_ks']:.3f}")
    c[3].metric("Brier", f"{mt['test_brier']:.3f}")
    c[4].metric("Calibration error", f"{mt['test_ece']:.3f}")
    c[5].metric("PSI train→test", f"{mt['psi_train_vs_test']:.3f}", help=am.psi_label(mt["psi_train_vs_test"]))
    st.caption(f"{m['n_train']} train / {m['n_calibration']} calibration / {m['n_test']} test (chronological). "
               f"Train {m['period_train'][0]} to {m['period_train'][1]}; test {m['period_test'][0]} to "
               f"{m['period_test'][1]}. Approval rate train {mt['base_rate_train']:.1%}, test {mt['base_rate_test']:.1%}.")
    c1, c2 = st.columns(2)
    roc = pd.DataFrame(m["roc"])
    c1.altair_chart((alt.Chart(roc).mark_line(color=COLORS["info"]).encode(x=alt.X("fpr:Q", title="False positive rate"),
                                                                           y=alt.Y("tpr:Q", title="True positive rate"))
                     + alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]})).mark_line(strokeDash=[4, 4], color="#999")
                     .encode(x="x:Q", y="y:Q")).properties(height=280, title="ROC curve (test period)"), width="stretch")
    rel = pd.DataFrame(m["reliability"])
    c2.altair_chart((alt.Chart(rel).mark_line(point=True, color=COLORS["amber"]).encode(
        x=alt.X("mean_predicted:Q", title="Predicted probability", scale=alt.Scale(domain=[0, 1])),
        y=alt.Y("observed_rate:Q", title="Observed approval rate", scale=alt.Scale(domain=[0, 1])),
        tooltip=["bin", "n", "mean_predicted", "observed_rate"])
        + alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]})).mark_line(strokeDash=[4, 4], color="#999")
        .encode(x="x:Q", y="y:Q")).properties(height=280, title="Reliability (calibration) diagram"), width="stretch")
    iv = pd.DataFrame([{"Feature": am.FEATURE_BY_NAME[f].label, "IV": v, "Strength": am.iv_strength(v),
                        "In model": f in m["features"], "Dropped because": m["dropped"].get(f, "")}
                       for f, v in m["iv"].items()]).sort_values("IV", ascending=False)
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(iv).mark_bar().encode(x=alt.X("IV:Q", title="Information Value"),
                                                    y=alt.Y("Feature:N", sort="-x", title=None),
                                                    color=alt.Color("In model:N")).properties(
        height=320, title="Information Value by feature"), width="stretch")
    c2.dataframe(iv, hide_index=True, width="stretch")
    st.markdown(f"**Scorecard** (PDO {m['hyperparameters']['pdo']:g}, {m['hyperparameters']['base_score']:g} points "
                f"at odds {m['hyperparameters']['base_odds']:g}:1; base points {m['base_points']:.0f})")
    sc = pd.DataFrame(m["scorecard"])[["label", "bin", "train_n", "woe", "points"]]
    sc.columns = ["Feature", "Bin", "Training cases", "WoE", "Points"]
    st.dataframe(sc.round(3), hide_index=True, width="stretch")
    st.markdown("**Subgroup monitoring** (attributes are not model inputs; adverse-impact ratio < 0.8 is flagged)")
    sg = pd.DataFrame(m["subgroups"])
    sg["flag"] = sg["adverse_impact_ratio"].apply(lambda v: "REVIEW (< 0.8)" if v is not None and v < 0.8 else "")
    st.dataframe(sg.round(3), hide_index=True, width="stretch")


def page_model():
    hero("Model lab", "Approval probability from the bank's own past decisions - shown only if it passes the gate")
    st.caption("Trains a calibrated logistic scorecard on THIS bank's historical decisions: WoE binning with "
               "monotonic merging, L2-regularised logistic regression (IRLS), points scaling, Platt or isotonic "
               "calibration, and out-of-time validation. A model is used only if it passes the bank's gate "
               f"(>= {cfg.model_gate.min_samples} training cases, AUC >= {cfg.model_gate.min_test_auc}, calibration "
               f"error <= {cfg.model_gate.max_test_ece}). The probability reflects the bank's past decisions, "
               "including any bias in them, and never overrides a rule outcome.")
    active = am.load_model(bank_id)
    if active:
        if active.get("policy_version_at_training") != cfg.meta.policy_version:
            st.warning("This model is inactive because the bank policy changed after training. "
                       "Train and activate a new model under the current policy.")
        with st.expander(f"Active model for {cfg.meta.display_name}: {active['model_id']}", expanded=False):
            show_model(active, "Active model")
            if st.button("Remove active model"):
                am.delete_model(bank_id)
                st.rerun()
    else:
        st.info("No model saved for this bank - the assessment shows no approval probability.")

    for k, v in dict(ml_n=3000, ml_seed=7, ml_bins=5, ml_l2=1.0, ml_pdo=20, ml_base=600,
                     ml_src="Synthetic history (demo)", ml_cal="platt").items():
        ss.setdefault(k, v)
    st.subheader("Train a new model")
    src = st.radio("Training data", ["Synthetic history (demo)", "Upload bank history (CSV)"], horizontal=True,
                   key="ml_src")
    history, synthetic, source = None, True, "synthetic"
    if src.startswith("Synthetic"):
        c1, c2 = st.columns(2)
        n = c1.slider("Number of synthetic decisions", 600, 6000, step=200, key="ml_n")
        seed = c2.number_input("Random seed", 0, 10_000, key="ml_seed")
        history = generate(cfg, n=int(n), seed=int(seed))
        st.caption("Synthetic applicants with an invented decision process (see synthetic_history.py).")
    else:
        template = generate(cfg, n=5, seed=1).to_csv(index=False)
        st.download_button("Download CSV template", template, file_name="apex_history_template.csv", mime="text/csv")
        up = st.file_uploader("Historical decisions CSV (decision = 1 approved / 0 declined, decision_date)",
                              type="csv")
        if up is not None:
            try:
                history = pd.read_csv(io.BytesIO(up.getvalue()))
                synthetic, source = False, f"uploaded history ({up.name})"
                st.caption(f"{len(history)} rows loaded. PAN columns, if present, are ignored.")
                history = history.drop(columns=[c for c in history.columns if c.lower() == "pan"])
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read the file: {e}")
    c1, c2, c3, c4, c5 = st.columns(5)
    n_bins = c1.slider("Bins per feature", 3, 10, key="ml_bins")
    l2 = c2.select_slider("L2 penalty", [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0], key="ml_l2")
    calib = c3.selectbox("Calibration", ["platt", "isotonic"], key="ml_cal")
    pdo = c4.number_input("Points to double odds (PDO)", 5, 100, key="ml_pdo")
    base = c5.number_input("Base score", 100, 1000, key="ml_base")
    if st.button("Train model", type="primary", disabled=history is None):
        with st.spinner("Assessing history, binning, fitting and validating..."):
            try:
                ss.ml_candidate = am.train(history, cfg, source, synthetic, n_bins=n_bins, l2=float(l2),
                                           calibration=calib, pdo=float(pdo), base_score=float(base))
            except Exception as e:  # noqa: BLE001
                st.error(f"Training failed: {e}")
    cand = ss.get("ml_candidate")
    if cand and cand["bank_id"] == bank_id:
        show_model(cand, f"Candidate model {cand['model_id']}")
        if cand["gate"]["passed"] and st.button("Activate this model for " + cfg.meta.display_name, type="primary"):
            am.save_model(cand)
            ss.pop("ml_candidate")
            st.success("Model activated. Assessments for this bank now show its probability.")
            st.rerun()


{PAGES[0]: page_input, PAGES[1]: page_assessment, PAGES[2]: page_decision, PAGES[3]: page_explain,
 PAGES[4]: page_simulator, PAGES[5]: page_report, PAGES[6]: page_policy, PAGES[7]: page_model,
 PAGES[8]: page_guide}[ss.page]()
