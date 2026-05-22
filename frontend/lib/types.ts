export type CardStatus =
  | "proposed"
  | "planned"
  | "running"
  | "reviewing"
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
  step?: number | null;
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
  executor_context?: Record<string, unknown> | null;
}

export interface Asset {
  asset_id: string;
  asset_type: string;
  title: string;
  status: string;
  created_by_run?: string | null;
  path: string;
  artifact_id?: string | null;
  depends_on?: string[];
  summary: string;
  report_selected: boolean;
  metadata: Record<string, unknown>;
}

export interface AssetFlowCardNode {
  card_id: string;
  title: string;
  status: CardStatus;
  card_type: string;
  linked_modules: string[];
}

export interface AssetFlowAssetNode {
  asset_id: string;
  asset_type: string;
  title: string;
  status: string;
  created_by_run?: string | null;
  depends_on: string[];
  summary: string;
  path: string;
}

export interface AssetFlowCardEdge {
  edge_id: string;
  edge_type: "card_output_to_input" | "raw_asset_to_card";
  source_card_id?: string | null;
  target_card_id: string;
  asset_id: string;
  asset_title: string;
  asset_status: string;
  label: string;
}

export interface AssetFlowAssetEdge {
  edge_id: string;
  edge_type: "asset_lineage";
  source_asset_id: string;
  target_asset_id: string;
  source_card_id?: string | null;
  target_card_id?: string | null;
  source_asset_title: string;
  target_asset_title: string;
}

export interface AssetFlow {
  project_id: string;
  cards: AssetFlowCardNode[];
  assets: AssetFlowAssetNode[];
  card_edges: AssetFlowCardEdge[];
  asset_edges: AssetFlowAssetEdge[];
}

export interface WorkItem {
  card_id: string;
  title: string;
  status: CardStatus;
  card_type: string;
  step?: number;
  required_asset_ids: string[];
  produced_asset_ids: string[];
  depends_on_card_ids: string[];
  blocked_by_card_ids: string[];
  blocked_by_asset_ids: string[];
  planned_input_asset_ids?: string[];
  can_start: boolean;
  block_reasons: string[];
  active: boolean;
}

export interface WorkOrder {
  project_id: string;
  work_items: WorkItem[];
  parallel_batches: Array<{ batch_index: number; card_ids: string[] }>;
  dependency_edges: Array<{
    edge_id: string;
    source_card_id: string;
    target_card_id: string;
    edge_type: "work_dependency";
  }>;
  cycle_card_ids: string[];
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

export interface ChatUploadResponse {
  asset: Asset;
  attachment: {
    type: "asset";
    id: string;
    label: string;
  };
}

export interface ChatSessionMessageRecord {
  id: string;
  role: "user" | "manager";
  content: string;
  proposal?: Proposal | null;
  thinking?: string | null;
  attachments?: Array<{ type: "card" | "asset"; id: string; label: string }> | null;
  state?: "idle" | "thinking" | "streaming" | "done" | "error" | null;
}

export interface ChatSessionSummary {
  session_id: string;
  summary: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatSessionMessageRecord[];
}

export interface ExecutionFileEntry {
  path: string;
  name: string;
  category:
    | "task_packet"
    | "adapter_contract"
    | "executor_brief"
    | "executor_prompt"
    | "manifest"
    | "filesystem_audit"
    | "manager_brief"
    | "review_context"
    | "transcript"
    | "agent_trace"
    | "agent_output_timeline"
    | "generated_script"
    | string;
  run_id?: string | null;
  size_bytes: number;
  updated_at: number;
}

export interface ProjectFiles {
  data_assets: Asset[];
  session_uploads: Asset[];
  execution_files: ExecutionFileEntry[];
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

export interface ProjectState {
  project_id: string;
  name: string;
  status: string;
  schema_version: string;
  current_goal: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends ProjectState {
  card_counts: Record<string, number>;
  result_counts: Record<string, number>;
}

export interface WorkerCapability {
  worker_type: string;
  configured: boolean;
  requires_configuration: boolean;
  declares_network_access: boolean;
  execution_mode: string;
  launch_template_setting?: string | null;
  wrapper_module?: string | null;
  provider?: string | null;
  resolved_launch_template?: string | null;
  recommended_launch_examples: string[];
  notes: string[];
}

export interface PythonRuntime {
  name: string;
  label: string;
  path?: string | null;
  manager: string;
  exists: boolean;
}

export interface CreateProjectPayload {
  project_id: string;
  name: string;
  current_goal: string;
}

export interface ProjectSnapshot {
  summary: ProjectSummary;
  project: ProjectState;
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
  worker_capabilities?: WorkerCapability[];
  python_runtimes?: PythonRuntime[];
}

export interface RunRecord {
  run_id: string;
  card_id: string;
  module_id?: string | null;
  status: "queued" | "running" | "reviewing" | "needs_approval" | "success" | "failed" | "cancelled" | "reviewed";
  title: string;
  summary: string;
  started_at: string;
  finished_at?: string | null;
  worker_type: string;
  cancel_reason?: string | null;
  archived_at?: string | null;
  cleanup_status?: "pending" | "completed" | null;
  needs_manager_attention?: boolean;
}

export interface StartRunResponse {
  run_id: string;
  card_id: string;
  worker_type: string;
  status: "queued" | "needs_approval" | "cancelled";
  latest_event?: RunEvent;
  pending_approvals?: RuntimeApprovalDecision[];
  rejected_approvals?: RuntimeApprovalDecision[];
}

export interface RunEvent {
  event_id: string;
  run_id: string;
  card_id: string;
  message: string;
  event_type: string;
  created_at: string;
  payload?: Record<string, unknown>;
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
