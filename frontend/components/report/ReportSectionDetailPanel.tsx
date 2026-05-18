"use client";

import { ReportSection } from "@/lib/types";

export function ReportSectionDetailPanel({ section }: { section?: ReportSection }) {
  if (!section) {
    return (
      <section className="panel">
        <div className="panel-header">
          <h3>Section Detail</h3>
          <span>No selection</span>
        </div>
        <div className="panel-body">
          <div className="empty-state">选择一个报告章节查看引用的 assets 和 claims。</div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>{section.title}</h3>
        <span>{section.section}</span>
      </div>
      <div className="panel-body stack">
        <div className="meta-block">
          <h4>Summary</h4>
          <div>{section.summary}</div>
        </div>
        <div className="meta-block">
          <h4>Assets</h4>
          <div className="kv">
            {section.assets.length ? section.assets.map((asset) => <div key={asset.asset_id}>{asset.title}</div>) : <div className="muted">No assets</div>}
          </div>
        </div>
        <div className="meta-block">
          <h4>Claims</h4>
          <div className="kv">
            {section.claims.length ? section.claims.map((claim) => <div key={claim.claim_id}>{claim.text}</div>) : <div className="muted">No claims</div>}
          </div>
        </div>
      </div>
    </section>
  );
}
