from __future__ import annotations

from html import escape

from app.models.graph import ReportItem
from app.services.project_service import ProjectService


class ReportService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def build_report(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        asset_map = {asset.asset_id: asset for asset in graph.assets}
        claim_map = {claim.claim_id: claim for claim in graph.claims}
        sections = []
        referenced_asset_ids = set()
        referenced_claim_ids = set()
        for item in graph.report_items:
            assets = [asset_map[asset_id] for asset_id in item.linked_asset_ids if asset_id in asset_map]
            claims = [claim_map[claim_id] for claim_id in item.linked_claim_ids if claim_id in claim_map]
            referenced_asset_ids.update(item.linked_asset_ids)
            referenced_claim_ids.update(item.linked_claim_ids)
            sections.append(
                {
                    "item_id": item.item_id,
                    "section": item.section,
                    "title": item.title,
                    "summary": item.summary,
                    "assets": assets,
                    "claims": claims,
                }
            )

        selected_assets = [
            asset
            for asset in graph.assets
            if asset.report_selected and asset.asset_id not in referenced_asset_ids and asset.status == "valid"
        ]
        for asset in selected_assets:
            sections.append(
                {
                    "item_id": f"report_selected_asset_{asset.asset_id}",
                    "section": "Selected Results",
                    "title": asset.title,
                    "summary": asset.summary,
                    "assets": [asset],
                    "claims": [],
                }
            )
        selected_claims = [
            claim
            for claim in graph.claims
            if claim.report_selected and claim.claim_id not in referenced_claim_ids and claim.status == "valid"
        ]
        for claim in selected_claims:
            sections.append(
                {
                    "item_id": f"report_selected_claim_{claim.claim_id}",
                    "section": "Selected Claims",
                    "title": claim.text[:64],
                    "summary": claim.text,
                    "assets": [asset_map[item] for item in claim.depends_on_assets if item in asset_map],
                    "claims": [claim],
                }
            )
        return {"project": snapshot["project"], "sections": sections}

    def reorder_sections(self, project_id: str, item_ids: list[str]) -> dict:
        store = self.project_service.graph_store(project_id)
        report_items = store.load_report_items()
        item_map = {item.item_id: item for item in report_items}
        reordered = [item_map[item_id] for item_id in item_ids if item_id in item_map]
        remaining = [item for item in report_items if item.item_id not in item_ids]
        store.save_report_items(reordered + remaining)
        return self.build_report(project_id)

    def export_html(self, project_id: str) -> dict:
        report = self.build_report(project_id)
        project = report["project"]
        sections = report["sections"]
        html = [
            "<html><head><meta charset='utf-8'><title>Blueprint Report</title></head><body>",
            f"<h1>{escape(project.name)}</h1>",
            f"<p>{escape(project.current_goal)}</p>",
        ]
        for section in sections:
            html.append(f"<section><h2>{escape(str(section['title']))}</h2>")
            html.append(f"<p>{escape(str(section['summary']))}</p>")
            assets = section["assets"]
            claims = section["claims"]
            if assets:
                html.append("<h3>Assets</h3><ul>")
                for asset in assets:
                    html.append(f"<li>{escape(asset.title)} ({escape(asset.path)})</li>")
                html.append("</ul>")
            if claims:
                html.append("<h3>Claims</h3><ul>")
                for claim in claims:
                    html.append(f"<li>{escape(claim.text)}</li>")
                html.append("</ul>")
            html.append("</section>")
        html.append("</body></html>")
        payload = "\n".join(html)
        path = self.project_service.project_path(project_id) / "reports" / "report.html"
        path.write_text(payload, encoding="utf-8")
        return {"path": str(path.relative_to(self.project_service.project_path(project_id))), "html": payload}
