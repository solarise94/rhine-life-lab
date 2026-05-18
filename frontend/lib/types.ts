export type CardStatus =
  | "proposed"
  | "planned"
  | "running"
  | "needs_review"
  | "accepted"
  | "rejected"
  | "stale"
  | "superseded"
  | "cancelled"
  | "failed";

export interface CardRef {
  label: string;
  asset_id?: string | null;
  status?: string | null;
}

export interface Card {
  card_id: string;
  card_type: string;
  title: string;
  status: CardStatus;
  aggregate_status?: string | null;
  summary: string;
  why: string;
  inputs: CardRef[];
  outputs: CardRef[];
  key_findings: string[];
  manager_review: string;
  next_actions: string[];
  linked_modules: string[];
  linked_runs: string[];
  linked_assets: string[];
  progress_note?: string | null;
}

export interface Asset {
  asset_id: string;
  asset_type: string;
  title: string;
  status: string;
  path: string;
  summary: string;
  report_selected: boolean;
  metadata: Record<string, unknown>;
}

export interface AssetPreview {
  kind: "missing" | "image" | "table" | "markdown" | "text" | "binary";
  content_type?: string | null;
  text?: string | null;
  table?: { columns: string[]; rows: string[][] } | null;
  content_url?: string | null;
  size_bytes?: number | null;
}

export interface AssetDetail {
  asset: Asset;
  preview: AssetPreview;
}

export interface Proposal {
  proposal_id: string;
  patch_id: string;
  title: string;
  summary: string;
  impact_summary: string;
  status: string;
  consistency_warnings: string[];
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  status: string;
  schema_version: string;
  current_goal: string;
  created_at: string;
  updated_at: string;
  card_counts: Record<string, number>;
  result_counts: Record<string, number>;
}

export interface ProjectSnapshot {
  summary: ProjectSummary;
  project: ProjectSummary;
  cards: Card[];
  graph: {
    modules: Array<Record<string, unknown>>;
    assets: Asset[];
    claims: Array<Record<string, unknown>>;
    runs: RunRecord[];
    report_items: Array<Record<string, unknown>>;
    metadata: Record<string, unknown>;
  };
  proposals: Proposal[];
  git_log: Array<{ hash: string; date: string; subject: string }>;
}

export interface RunRecord {
  run_id: string;
  card_id: string;
  module_id?: string | null;
  status: string;
  title: string;
  summary: string;
  started_at: string;
  finished_at?: string | null;
  worker_type: string;
}

export interface RunEvent {
  event_id: string;
  run_id: string;
  card_id: string;
  message: string;
  event_type: string;
  created_at: string;
}

export interface RuntimeApprovalDecision {
  request_id: string;
  target: string;
  action: string;
  risk_level: string;
  decision: string;
  reason: string;
  user_required: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReportSection {
  item_id: string;
  section: string;
  title: string;
  summary: string;
  assets: Asset[];
  claims: Array<{ claim_id: string; text: string; status: string }>;
}
