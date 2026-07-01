# Fund Flow Fraud Detection

An end-to-end fraud detection system for India's digital payment rails (UPI, NEFT, RTGS, IMPS), combining a 3-model ensemble classifier with graph-based fund-flow analysis to detect suspicious transaction chains — round-tripping, layering, fan-out, and fan-in (mule account) patterns.

Built on the PaySim dataset (6.36M transactions), mapped to Indian payment-rail context with RBI threshold logic and INR scaling.

---

## Highlights

- **Ensemble fraud model** — XGBoost + Isolation Forest, AUC scores of **0.9998** and **0.9105** on held-out data
- **30 engineered features** across transaction, account, and behavioral dimensions
- **Graph analytics** (in progress) — NetworkX-based detection of fraud rings and mule networks
- **Explainability** — SHAP-based reasoning for every flagged transaction
- **Real-time dashboard** — Flask + SSE live transaction feed with risk scoring
- **Investigator-ready exports** — PDF reports in FIU filing format

---

## Project Status

| Phase | Status |
|---|---|
| EDA | 
| Model Training (3 models + ensemble) 
| Git Push (models, notebooks, configs)
| Graph Analytics (NetworkX)
| Flask Backend + API 
| Frontend Dashboard (4 pages)
| PDF Export
| Synthetic Fraud Chain Injection 
| Testing + Polish 

---

## Tech Stack

- **Modeling:** XGBoost, Isolation Forest, scikit-learn, SHAP
- **Graph Analytics:** NetworkX, PyVis
- **Backend:** Flask, Server-Sent Events (SSE)
- **Frontend:** HTML/CSS/JS (dark theme dashboard)
- **Reporting:** ReportLab (PDF generation)
- **Data:** PaySim, Faker (synthetic fraud chain injection)

---

## Fraud Patterns Detected (Graph Analytics)

Using NetworkX, accounts are modeled as nodes and transactions as weighted edges to detect:

- **Round-trip** — A → B → C → A
- **Fan-out** — A → B, A → C, A → D (same time window)
- **Fan-in** — B → Z, C → Z, D → Z (mule account signature)
- **Layering** — A → B → C → D → E (multi-hop chains)

Synthetic patterns (500 round-trip chains, 500 layering patterns, 500 structuring cases) are injected via Faker for demo and testing purposes.

---

## Planned Dashboard

| Page | Purpose |
|---|---|
| `index.html` | Live transaction feed, risk score cards, alert queue |
| `account.html` | Account lookup, transaction history, network graph, SHAP explanation, PDF export |
| `analytics.html` | Fraud-by-type charts, amount distribution, time-based patterns, risk heatmap |
| `graph.html` | Full fund-flow network view with risk/date/amount filters |

---

---

## Explainability

Each flagged transaction is paired with a SHAP-based explanation, surfaced directly on the dashboard, e.g.:

> Flagged because — Amount was 4x the account average, transaction occurred at 2 AM, and the account is a student account.

---

## Roadmap

1. Push models, notebooks, and configs to GitHub
2. Build fund-flow graph and detect fraud ring patterns (NetworkX)
3. Build Flask backend with real-time scoring API
4. Build 4-page dashboard (alerts, account lookup, analytics, network view)
5. Integrate SHAP explanations into the scoring pipeline
6. Add PDF export in FIU filing format
7. Inject synthetic fraud chains for demo purposes
8. Final testing, UI polish, and documentation

---

## Setup

```bash
git clone <repo-url>
cd fund-flow-fraud
pip install -r requirements.txt
python app.py
```

---

## License

MIT