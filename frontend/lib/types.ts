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

export type ArtifactClass = "figure" | "table" | "document" | "model" | "archive" | "binary";

export interface CardOutputSpec {
  role: string;
  label: string;
  artifact_class: ArtifactClass;
  accepted_formats?: string[] | null;
  preferred_format?: string | null;
  asset_id?: string | null;
  status?: string | null;
  required?: boolean;
  description?: string | null;
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
  outputs: CardOutputSpec[];
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
  missing_script_asset_requirement_ids?: string[];
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

export type ArtifactPreviewSource = "card" | "run" | "results" | "files" | "manager";

export interface ArtifactPreviewRequest {
  projectId: string;
  assetId?: string;
  runId?: string;
  cardId?: string;
  source: ArtifactPreviewSource;
}

export interface ArtifactPreviewState {
  open: boolean;
  loading: boolean;
  error?: string;
  detail?: AssetDetail;
  source?: ArtifactPreviewRequest;
}

export interface ChatUploadResponse {
  asset: Asset;
  attachment: {
    type: "asset";
    id: string;
    label: string;
  };
}

export interface ChatSessionMessageTimelineItem {
  id: string;
  kind: string;
  content?: string | null;
  label?: string | null;
  tool_name?: string | null;
  status?: string | null;
  started_at?: number | null;
  ended_at?: number | null;
  first_kept_message_id?: string | null;
  tokens_before?: number | null;
  tokens_after?: number | null;
  duration_ms?: number | null;
  provider?: string | null;
  model?: string | null;
}

export interface ChatTokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
  context_window_tokens?: number | null;
  max_output_tokens?: number | null;
}

export interface ChatSessionMessageRecord {
  id: string;
  role: "user" | "manager";
  content: string;
  proposal?: Proposal | null;
  thinking?: string | null;
  attachments?: Array<{ type: "card" | "asset"; id: string; label: string }> | null;
  state?: "idle" | "thinking" | "streaming" | "done" | "error" | null;
  timeline?: ChatSessionMessageTimelineItem[] | null;
  token_usage?: ChatTokenUsage | null;
}

export interface ChatSessionSummary {
  session_id: string;
  summary: string;
  created_at: string;
  updated_at: string;
  revision: number;
  auto_owner?: boolean | null;
  auto_mode_state?: string | null;
  btw_mode?: boolean | null;
  message_count: number;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatSessionMessageRecord[];
}

export interface ManagerAutoDirective {
  id: string;
  message_id?: string | null;
  text: string;
  created_at: string;
  status: string;
  resolved_at?: string | null;
  resolution_note?: string | null;
}

export interface ManagerAutoChainLimitBasis {
  executable_card_count: number;
  formula: string;
}

export interface ManagerAutoState {
  enabled: boolean;
  mode: "continuous" | "once";
  owner_session_id?: string | null;
  state: "idle" | "running" | "thinking" | "stopped";
  started_at?: string | null;
  last_wake_id?: string | null;
  chain_count: number;
  max_chain_count: number;
  chain_limit_basis: ManagerAutoChainLimitBasis;
  active_run_id?: string | null;
  active_job_id?: string | null;
  stopped_at?: string | null;
  stop_reason?: string | null;
  stop_message?: string | null;
  pending_directives: ManagerAutoDirective[];
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
    | "dependency_issue"
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
  active_data_assets?: Asset[];
  stale_data_assets?: Asset[];
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
  runtime_preferences: ProjectRuntimePreferences;
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

export interface ExecutorProfile {
  profile_id: string;
  display_name: string;
  worker_type: string;
  auth_mode: "cli_native" | "project_api";
  enabled: boolean;
  command?: string | null;
  api_protocol?: string | null;
  provider_id?: string | null;
  model?: string | null;
  base_url?: string | null;
  credential_ref?: string | null;
  permission_preset?: string;
  native_auth_readonly?: boolean;
}

export interface ExecutorProfileListResponse {
  profiles: ExecutorProfile[];
  support_matrix: {
    auth_modes: Record<string, string[]>;
    api_protocols: Record<string, string[]>;
    command_configured: Record<string, boolean>;
  };
}

export interface ExecutorProfileValidation {
  profile_id: string;
  valid: boolean;
  errors: string[];
  warnings: string[];
  cli_available?: boolean | null;
  auth_configured?: boolean | null;
  provider_configured?: boolean | null;
}

export interface PythonRuntime {
  name: string;
  label: string;
  path?: string | null;
  manager: string;
  exists: boolean;
}

export type RRuntime = PythonRuntime;

export interface CreateProjectPayload {
  project_id: string;
  name: string;
  current_goal: string;
}

export interface AppSettings {
  deepseek: {
    api_key_configured: boolean;
    api_base_url: string;
    pi_base_url: string;
    manager_model: string;
    executor_model: string;
    reviewer_model: string;
    library_summarizer_model: string;
  };
  web_search: {
    enabled: boolean;
    api_key_configured: boolean;
    base_url: string;
  };
  anthropic: {
    api_key_configured: boolean;
    api_base_url: string;
  };
  openai: {
    api_key_configured: boolean;
    api_base_url: string;
  };
}

export interface UpdateAppSettingsPayload {
  deepseek_api_key?: string | null;
  clear_deepseek_api_key?: boolean;
  deepseek_api_base_url?: string | null;
  pi_deepseek_base_url?: string | null;
  manager_model?: string | null;
  executor_model?: string | null;
  reviewer_model?: string | null;
  library_summarizer_model?: string | null;
  manager_websearch_enabled?: boolean | null;
  tavily_api_key?: string | null;
  clear_tavily_api_key?: boolean;
  tavily_base_url?: string | null;
  anthropic_api_key?: string | null;
  clear_anthropic_api_key?: boolean;
  anthropic_api_base_url?: string | null;
  openai_api_key?: string | null;
  clear_openai_api_key?: boolean;
  openai_api_base_url?: string | null;
}

export interface UpdateProjectRuntimePreferencesPayload {
  script_preference?: "auto" | "prefer_python" | "prefer_r" | "prefer_mixed" | null;
  python_runtime?: string | null;
  r_runtime?: string | null;
}

export interface ProjectRuntimePreferences {
  script_preference: "auto" | "prefer_python" | "prefer_r" | "prefer_mixed";
  python_runtime?: string | null;
  r_runtime?: string | null;
}

export interface LibraryEntry {
  id: string;
  kind?: "skill" | "mcp";
  name: string;
  summary: string;
  summary_short?: string;
  summary_long?: string;
  tags: string[];
  use_cases?: string[];
  enabled: boolean;
  source?: string | null;
  source_path?: string | null;
  source_hash?: string | null;
  compatibility_notes?: string[] | null;
  runtime_requirements?: string[] | null;
  supported_runtimes?: string[] | null;
  launch_hint?: string | null;
  generated_by?: string | null;
  generated_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface LibraryListResponse {
  kind: "skill" | "mcp";
  items: LibraryEntry[];
  summary: string;
  updated_at?: string | null;
  project_id?: string;
}

export interface LibraryDetailResponse {
  kind: "skill" | "mcp";
  item: LibraryEntry;
  updated_at?: string | null;
  project_id?: string;
}

export interface ReportExportResponse {
  path: string;
  content_url: string;
  html?: string;
}

export interface DiagnosticExportResponse {
  path: string;
  download_url: string;
  created_at: string;
  run_count: number;
  session_count: number;
}

export interface ProjectSnapshot {
  summary: ProjectSummary;
  project: ProjectState;
  manager_auto?: ManagerAutoState;
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
  r_runtimes?: RRuntime[];
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
