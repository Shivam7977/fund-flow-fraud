import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pyvis.network import Network

from core.db import get_predictions_by_job, get_job

router = APIRouter(prefix="/predict", tags=["graph"])

# src/static/graphs/ — is file (src/routes/graph.py) ke relative se compute kiya
GRAPH_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "graphs")
os.makedirs(GRAPH_OUTPUT_DIR, exist_ok=True)

RISK_COLORS = {"FRAUD": "#e74c3c", "REVIEW": "#f39c12"}


def build_flagged_subgraph_html(job_id: str) -> str | None:
    """
    Poora graph nahi — sirf FRAUD/REVIEW flag hue transactions ka subgraph.
    (Project doc ke design ke mutabik: full graph load karna performance
    ke liye theek nahi, sirf suspicious cheezein dikhani hain.)
    Returns None agar is job mein koi flagged transaction nahi mila.
    """
    predictions = get_predictions_by_job(job_id)
    flagged = [p for p in predictions if p["risk_level"] in ("FRAUD", "REVIEW")]

    if not flagged:
        return None

    net = Network(
        height="700px", width="100%", directed=True,
        bgcolor="#1e1e1e", font_color="white", cdn_resources="in_line",
    )

    added_nodes = set()
    for p in flagged:
        for node in (p["nameOrig"], p["nameDest"]):
            if node not in added_nodes:
                net.add_node(node, label=node)
                added_nodes.add(node)

        # NOTE: "Rs." use kiya hai ₹ ki jagah — Windows ka default file
        # encoding (cp1252) ₹ symbol handle nahi karta, crash ho jaata hai.
        net.add_edge(
            p["nameOrig"], p["nameDest"],
            value=float(p["amount_inr"]),
            title=f'Rs.{p["amount_inr"]:,.0f} | {p["risk_level"]} | score={p["final_score"]}',
            color=RISK_COLORS.get(p["risk_level"], "#95a5a6"),
        )

    output_path = os.path.join(GRAPH_OUTPUT_DIR, f"{job_id}.html")

    # PyVis ka apna write_html() Windows pe default (cp1252) encoding use karta
    # hai, jo non-ASCII characters pe crash karta hai. Isliye HTML string khud
    # generate karke UTF-8 mein explicitly likhte hain — safe on any OS.
    html_string = net.generate_html(notebook=False)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_string)

    return output_path


@router.get("/file/{job_id}/graph")
def get_flagged_graph(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mila")

    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job abhi ready nahi hai (status: {job['status']})")

    html_path = build_flagged_subgraph_html(job_id)
    if html_path is None:
        raise HTTPException(status_code=404, detail="Is upload mein koi FRAUD/REVIEW transaction flag nahi hua")

    return FileResponse(html_path, media_type="text/html")