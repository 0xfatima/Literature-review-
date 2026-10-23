export interface User {
  id: string;
  email: string;
  created_at: string;
}
export interface Project {
  id: string;
  title: string;
  topic_original: string;
  status: string;
  row_version: number;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}
export interface ProjectList {
  items: Project[];
  total: number;
  limit: number;
  offset: number;
}
export interface Conversation {
  id: string;
  project_id: string;
  title: string;
  title_source: "auto" | "user";
  message_count: number;
  row_version: number;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
}
export interface ConversationList {
  items: Conversation[];
  total: number;
  limit: number;
  offset: number;
}
export interface Message {
  id: string;
  conversation_id: string;
  seq: number;
  role: "user" | "assistant" | "system";
  author_id: string | null;
  content: string;
  payload: Record<string, unknown> | null;
  client_message_id: string | null;
  created_at: string;
}
export interface MessagePage {
  conversation: Conversation;
  items: Message[];
  has_older: boolean;
}
export interface MessagePosted {
  message: Message;
  conversation: Conversation;
}
export interface Job {
  id: string;
  project_id: string | null;
  type: string;
  status: string;
  progress: number;
  progress_message: string | null;
  attempts: number;
  cancel_requested: boolean;
  result: Record<string, unknown> | null;
  error_summary: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface JobList {
  items: Job[];
}

/* ---------- Phase 3: protocol and review workflow (backend: app/schemas/protocol.py, workflow.py) ---------- */
export interface FrameworkElement {
  key: string;
  label: string;
  value: string | null;
}
export interface ProtocolQuestion {
  stable_key?: string;
  text: string;
}
export interface Criterion {
  stable_key?: string;
  kind: "inclusion" | "exclusion";
  text: string;
  rationale?: string | null;
  origin?: "user" | "suggested";
  state?: "proposed" | "accepted" | "rejected";
}
export interface ProtocolContent {
  objective: string | null;
  framework: string | null;
  framework_source: string | null;
  framework_elements: FrameworkElement[];
  questions: ProtocolQuestion[];
  criteria: Criterion[];
  date_from: number | null;
  date_to: number | null;
  languages: string[];
  study_designs: string[];
  sources: string[];
  search_strategy: string | null;
  extraction_plan: string | null;
  synthesis_plan: string | null;
}
export interface Issue {
  field: string;
  severity: "error" | "warning";
  message: string;
}
export interface Impact {
  compared_with_version: number;
  changes: { field: string; change: string; stable_key?: string }[];
  earliest_stage: string | null;
  affected_stages: string[];
  triggers: Record<string, { fields: string[]; why: string }>;
  material_change_count: number;
}
export interface ProtocolVersion {
  id: string;
  version: number;
  status: "draft" | "approved" | "superseded";
  row_version: number;
  content: ProtocolContent;
  framework_rationale: string | null;
  issues: Issue[];
  can_approve: boolean;
  summary: string[];
  change_reason: string | null;
  supersedes_id: string | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
  approved_by: string | null;
  content_hash: string | null;
  impact: Impact | null;
  superseded_at: string | null;
}
export interface ProtocolVersionBrief {
  id: string;
  version: number;
  status: string;
  change_reason: string | null;
  created_at: string;
  approved_at: string | null;
  superseded_at: string | null;
}
export interface ProtocolOverview {
  draft: ProtocolVersion | null;
  approved: ProtocolVersion | null;
  versions: ProtocolVersionBrief[];
  stage_labels: Record<string, string>;
  field_labels: Record<string, string>;
}
export interface PendingQuestion {
  id: string;
  kind: "objective" | "framework" | "elements" | "questions" | "scope" | "criteria" | "review";
  step: number;
  stage: string;
  prompt: string;
  help: string;
  actions: string[];
  protocol_version_id: string;
  data?: {
    suggestion?: {
      framework: string;
      label: string;
      rationale: string;
      matched_terms: string[];
      method_note: string;
    };
    options?: { key: string; label: string; description: string }[];
    elements?: { key: string; label: string; help: string }[];
    suggested_question?: string | null;
    suggestions?: Criterion[];
  };
}
export interface WorkflowRun {
  id: string;
  project_id: string;
  conversation_id: string | null;
  stage: string;
  status: "queued" | "running" | "awaiting_input" | "idle" | "failed" | "needs_attention";
  pending_question_id: string | null;
  pending_question: PendingQuestion | null;
  protocol_version_id: string | null;
  iteration_count: number;
  max_iterations: number;
  stop_reason: string | null;
  error_summary: string | null;
  stage_before_revision: string | null;
  row_version: number;
}
export interface WorkflowState {
  run: WorkflowRun | null;
  stages: { id: string; label: string }[];
}

/* ---------- Phase 4: scholarly search and papers (backend: app/schemas/search.py) ---------- */
export type SearchTier = "open_access" | "additional";
export type SearchRunStatus = "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
export type SearchQueryStatus =
  "pending" | "running" | "completed" | "capped" | "partial" | "failed" | "cancelled";
export type AccessStatus = "open_pdf_link" | "open_landing_page" | "metadata_only";

export interface ProviderInfo {
  key: string;
  label: string;
  tier: SearchTier;
  description: string;
  attribution: string;
  terms_url: string;
  aliases: string[];
}
export interface PlannedQuery {
  provider: string;
  provider_label: string;
  query_text: string;
  endpoint: string;
  params: Record<string, unknown>;
  url: string;
  filters_applied: Record<string, unknown>;
  limitations: string[];
}
export interface ProtocolSources {
  listed: string[];
  matched: string[];
  unsupported: string[];
  added_beyond_protocol: string[];
}
export interface SearchPlan {
  protocol_version_id: string;
  protocol_version: number;
  tier: SearchTier;
  expression: string;
  expression_source: string;
  derived_expression: string;
  derivation_notes: string[];
  filters: Record<string, unknown>;
  providers: string[];
  protocol_sources: ProtocolSources;
  limitations: string[];
  queries: PlannedQuery[];
  ready: boolean;
  max_records_per_provider: number;
  catalog: ProviderInfo[];
}
export interface SearchQueryRecord {
  id: string;
  provider: string;
  provider_label: string;
  attribution: string | null;
  endpoint: string;
  query_text: string;
  params: Record<string, unknown>;
  url: string;
  filters_applied: Record<string, unknown>;
  limitations: string[];
  status: SearchQueryStatus;
  pages_fetched: number;
  request_count: number;
  total_reported: number | null;
  records_retrieved: number;
  records_skipped: number;
  skipped: { provider_record_id: string | null; reason: string }[];
  error_kind: string | null;
  error_summary: string | null;
  started_at: string | null;
  retrieved_at: string | null;
  finished_at: string | null;
}
export interface SearchRun {
  id: string;
  tier: SearchTier;
  tier_label: string;
  status: SearchRunStatus;
  protocol_version_id: string;
  is_current_protocol: boolean;
  expression: string;
  expression_source: string;
  derived_expression: string | null;
  derivation_notes: string[];
  filters: Record<string, unknown>;
  providers: string[];
  protocol_sources: ProtocolSources;
  limitations: string[];
  records_retrieved: number;
  records_skipped: number;
  papers_new: number;
  papers_matched_existing: number;
  duplicates_in_run: number;
  possible_duplicates: number;
  stop_reason: string | null;
  error_summary: string | null;
  attempt_count: number;
  broaden_decision: "yes" | "no" | "later" | null;
  broaden_decided_at: string | null;
  job_id: string | null;
  row_version: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  queries: SearchQueryRecord[];
}
export interface SearchRunList {
  items: SearchRun[];
}
export interface IdentificationCounts {
  records_identified: number;
  records_by_provider: Record<string, number>;
  records_by_tier: Record<string, number>;
  unique_papers: number;
  duplicates_removed: number;
  possible_duplicates: number;
  preprints: number;
  access: Partial<Record<AccessStatus, number>>;
}
export interface SearchSummary {
  all_runs: IdentificationCounts;
  current_protocol: IdentificationCounts | null;
  approved_protocol_version_id: string | null;
  run_count: number;
  incomplete_runs: number;
  stale_runs: number;
  broaden_prompt: { search_run_id: string; decision: "yes" | "no" | "later" | null } | null;
  counting_note: string;
}
export interface PaperBrief {
  id: string;
  title: string;
  authors: string[];
  author_count: number;
  year: number | null;
  venue: string | null;
  publication_type: string | null;
  doi: string | null;
  pmid: string | null;
  pmcid: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  is_preprint: boolean;
  has_preprint_version: boolean;
  is_retracted: boolean | null;
  oa_status: "open" | "closed" | "unknown";
  access_status: AccessStatus;
  access_label: string;
  full_text_status: string;
  full_text_label: string;
  oa_url: string | null;
  pdf_url: string | null;
  has_abstract: boolean;
  providers: string[];
  source_count: number;
  possible_duplicate_of_id: string | null;
  possible_duplicate_reason: string | null;
  created_at: string;
}
export interface PaperList {
  items: PaperBrief[];
  total: number;
  offset: number;
  limit: number;
}
export interface PaperSource {
  id: string;
  provider: string;
  provider_record_id: string;
  source_url: string | null;
  rank: number;
  match_basis: string;
  retrieved_at: string;
  search_run_id: string;
  search_run_created_at: string;
  search_run_tier: SearchTier;
  protocol_version_id: string;
  query_text: string;
  normalized: Record<string, unknown>;
  raw: Record<string, unknown>;
}
export interface PaperDetail extends PaperBrief {
  abstract: string | null;
  authors_full: { name: string; family: string | null; orcid: string | null }[];
  keywords: string[];
  language: string | null;
  license: string | null;
  other_ids: Record<string, unknown>;
  field_sources: Record<string, string>;
  possible_duplicate_of: { id: string; title: string; year: number | null; doi: string | null } | null;
  sources: PaperSource[];
}

/* ---------- Phase 5: relevance and screening (backend: app/schemas/screening.py) ---------- */
export type Band = "high" | "moderate_high" | "possible" | "low" | "insufficient" | "not_assessed";
export type HumanDecision = "include" | "exclude" | "uncertain";

export interface LexicalConcept {
  id: string;
  label: string;
  kind: "framework_element" | "research_question";
  text: string;
  score: number;
  matched: string[];
  missing: string[];
  best_field: "title" | "keywords" | "abstract" | null;
}
export interface CriterionJudgement {
  criterion: string;
  criterion_key: string;
  kind: "inclusion" | "exclusion";
  text: string;
  judgement: "met" | "not_met" | "unclear";
  evidence_quote: string | null;
  quote_verified: boolean;
}
export interface Assessment {
  id: string;
  method: "lexical" | "llm";
  method_version: string;
  score: number;
  band: Band;
  band_label: string;
  confidence: "normal" | "low";
  explanation: {
    method?: string;
    summary?: string;
    concepts?: LexicalConcept[];
    questions?: { question: string; text: string; relevance: "high" | "medium" | "low" | "none" }[];
    has_abstract?: boolean;
    insufficient_information?: boolean;
  };
  eligibility_suggestion: HumanDecision | null;
  suggestion_detail: { reason: string; criteria: CriterionJudgement[]; model_suggestion?: string } | null;
  warnings: string[];
  model_run_id: string | null;
  created_at: string;
}
export interface QueueItem {
  paper_id: string;
  title: string;
  authors: string[];
  author_count: number;
  year: number | null;
  venue: string | null;
  publication_type: string | null;
  abstract: string | null;
  keywords: string[];
  doi: string | null;
  providers: string[];
  is_preprint: boolean;
  is_retracted: boolean | null;
  access_status: AccessStatus;
  possible_duplicate_of_id: string | null;
  band: Band;
  band_label: string;
  band_source: "ai" | "lexical" | null;
  lexical: Assessment | null;
  ai: Assessment | null;
  decision: HumanDecision | null;
  decision_label: string;
  decision_id: string | null;
  decision_protocol_version_id: string | null;
  decision_is_current_protocol: boolean | null;
  state_row_version: number | null;
  decided_at: string | null;
}
export interface ScreeningProgress {
  total: number;
  undecided: number;
  include: number;
  exclude: number;
  uncertain: number;
}
export interface ScreeningQueue {
  items: QueueItem[];
  total: number;
  offset: number;
  limit: number;
  progress: ScreeningProgress;
  protocol_version_id: string;
  protocol_version: number;
}
export interface ScreeningReason {
  code: string;
  label: string;
  group: "exclusion_criterion" | "inclusion_not_met" | "generic";
}
export interface ScreeningDecisionRecord {
  id: string;
  decision: HumanDecision | "cleared";
  decision_label: string;
  reason_code: string | null;
  reason_label: string | null;
  note: string | null;
  reviewer_id: string | null;
  action: "decide" | "bulk" | "undo";
  batch_id: string | null;
  supersedes_id: string | null;
  protocol_version_id: string;
  assessment_ids: string[];
  created_at: string;
}
export interface RelevanceStatus {
  llm: {
    configured: boolean;
    provider: string | null;
    model: string | null;
    data_sent: string;
    provider_notice: string | null;
  };
  total_papers: number;
  protocol_version_id: string | null;
  counts: { lexical: Partial<Record<Band, number>>; llm: Partial<Record<Band, number>> };
  active_job_id: string | null;
  last_result: Record<string, unknown> | null;
  last_error: string | null;
  model_runs: {
    id: string;
    provider: string;
    model: string;
    prompt_version: string;
    status: "succeeded" | "failed";
    error_kind: string | null;
    error_summary: string | null;
    item_count: number;
    input_tokens: number | null;
    output_tokens: number | null;
    duration_ms: number | null;
    created_at: string;
  }[];
  methods: { lexical: string; llm: string };
}
