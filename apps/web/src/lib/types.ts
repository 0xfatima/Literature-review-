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
  documents: DocumentRecord[];
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

/* ---------- Phase 6: uploaded PDFs (backend: app/schemas/documents.py) ---------- */
export type ParseStatus = "queued" | "processing" | "parsed" | "failed";
export type MatchStatus = "unmatched" | "needs_confirmation" | "auto_matched" | "user_linked" | "confirmed";
export type ScanStatus = "not_scanned" | "clean" | "infected" | "error";
export type PageExtraction = "text" | "ocr" | "blank" | "unreadable";
export interface DocumentCandidate {
  paper_id: string;
  score: number | null;
  reasons: string[];
  title: string | null;
  year: number | null;
  doi: string | null;
}
export interface DocumentWarning {
  code: string;
  message: string;
  pages?: number[];
  mean_confidence?: number | null;
}
export interface DocumentRecord {
  id: string;
  paper_id: string | null;
  paper_title: string | null;
  original_filename: string;
  size_bytes: number;
  sha256: string;
  parse_status: ParseStatus;
  error_code: string | null;
  error_message: string | null;
  page_count: number | null;
  text_version: number;
  pages_text: number;
  pages_ocr: number;
  pages_blank: number;
  pages_unreadable: number;
  text_coverage: number | null;
  pdf_metadata: Record<string, string>;
  warnings: DocumentWarning[];
  scan_status: ScanStatus;
  scan_detail: string | null;
  match_status: MatchStatus;
  match_method: string | null;
  match_score: number | null;
  candidates: DocumentCandidate[];
  needs_confirmation: boolean;
  matched_at: string | null;
  can_retry: boolean;
  can_download: boolean;
  created_at: string;
  processed_at: string | null;
  row_version: number;
}
export interface DocumentList {
  items: DocumentRecord[];
  total: number;
  offset: number;
  limit: number;
}
export interface DocumentSummary {
  limits: {
    max_bytes: number;
    max_pages: number;
    quota_bytes_per_project: number;
    quota_files_per_project: number;
    quota_bytes_per_user: number;
    removed_retention_days: number;
    ocr_enabled: boolean;
    ocr_languages: string | null;
    malware_scanner: string;
    storage_backend: string;
  };
  usage: { project_bytes: number; project_files: number; user_bytes: number };
  counts: { parse: Record<string, number>; match: Record<string, number> };
}
export interface DocumentPage {
  page_no: number;
  text: string;
  extraction: PageExtraction;
  char_count: number;
  ocr_confidence: number | null;
  warnings: string[];
}
export interface DocumentPageList {
  document_id: string;
  parse_status: ParseStatus;
  text_version: number;
  page_count: number | null;
  total: number;
  offset: number;
  limit: number;
  items: DocumentPage[];
}

/* ---------- Phase 7: PRISMA flow ---------- */
export interface PrismaViolation {
  id: string; // V1–V11
  message: string;
  [key: string]: unknown;
}
export interface PrismaFlow {
  identified_a: number;
  identified_a_by_source: Record<string, number>;
  removed_duplicates: number;
  removed_automation: number;
  removed_other: number;
  screened: number;
  pending_ta: number;
  ta_excluded: number;
  ta_included_a: number;
  identified_b: number;
  identified_b_by_source: Record<string, number>;
  ta_included_b: number;
  sought_retrieval: number;
  pending_retrieval: number;
  not_retrieved: number;
  ft_assessed: number;
  pending_ft: number;
  ft_excluded: number;
  ft_excluded_by_reason: Record<string, number>;
  included_reports: number;
  included_studies: number;
  violations: PrismaViolation[];
  is_final: boolean;
  protocol_version_id: string | null;
}
export interface FtReason {
  code: string;
  label: string;
  group: string;
}

/* ---------- Phase 8: extraction schemas ---------- */
export type FieldType = "text" | "number" | "choice" | "multi_choice" | "boolean" | "date";
export interface FieldDefinition {
  field_key: string;
  label: string;
  type: FieldType;
  options?: string[];
  required?: boolean;
  hint?: string;
}
export interface ExtractionSchema {
  id: string;
  version: number;
  status: "draft" | "approved";
  field_definitions: FieldDefinition[];
  question_map: Record<string, string[]>;
  notes: string | null;
  approved_by: string | null;
  approved_at: string | null;
  change_reason: string | null;
  supersedes_id: string | null;
  created_by: string | null;
  row_version: number;
  created_at: string;
  updated_at: string;
  field_count: number;
}
export type EvidenceStatus =
  "pending" | "found" | "not_located" | "ambiguous" | "unavailable" | "not_applicable";
export type EvidenceReviewStatus = "unreviewed" | "approved" | "edited";
export type EvidenceOrigin = "manual" | "ai" | "system";
export interface ValidationWarning {
  code: string;
  message: string;
}
export interface EvidenceItem {
  id: string;
  paper_id: string;
  field_key: string;
  status: EvidenceStatus;
  value: unknown;
  excerpt: string | null;
  document_page_id: string | null;
  document_id: string | null;
  page_no: number | null;
  reviewer_id: string | null;
  model_run_id: string | null;
  note: string | null;
  origin: EvidenceOrigin;
  review_status: EvidenceReviewStatus;
  extraction_version: string | null;
  pages_inspected: number[];
  validation: ValidationWarning[];
  row_version: number;
  created_at: string;
  updated_at: string;
}
export interface EvidenceTableRow {
  paper: {
    id: string;
    title: string;
    year: number | null;
    doi: string | null;
    first_author: string;
    full_text_status: string;
    included: boolean;
    document_id: string | null;
  };
  cells: Record<string, EvidenceItem>;
}
export interface EvidenceTable {
  schema_id: string;
  schema_version: number;
  schema_status: "draft" | "approved";
  fields: FieldDefinition[];
  total: number;
  offset: number;
  limit: number;
  status_counts: Record<string, number>;
  needs_review: number;
  rows: EvidenceTableRow[];
}
export interface EvidenceRevision {
  revision: number;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown>;
  actor_id: string | null;
  model_run_id: string | null;
  reason: string | null;
  at: string;
}
export interface EvidenceHistory {
  item: EvidenceItem | null;
  revisions: EvidenceRevision[];
  outputs: { id: string; kind: string; created_at: string; revision: number; stale: boolean }[];
  source_page: {
    document_id: string;
    filename: string;
    page_no: number;
    text_version: number;
    is_current_text: boolean;
    text: string;
  } | null;
  model_run: {
    id: string;
    provider: string;
    model: string;
    prompt_version: string;
    created_at: string;
  } | null;
}
export interface ConsentStatus {
  provider: string | null;
  model: string | null;
  configured: boolean;
  operator_allows_documents: boolean;
  terms_reviewed: boolean;
  terms_record_version: string | null;
  notice_version: string;
  notice: string;
  consent: { id: string; consented_at: string; terms_record_version: string } | null;
  can_send_documents: boolean;
  blocked_reason: string | null;
}
export interface ExtractionRunStatus {
  schema_id: string;
  schema_status: string;
  prompt_version: string;
  consent: ConsentStatus;
  included_papers: number;
  active_job_id: string | null;
  last_result: (Record<string, unknown> & { message?: string | null; stop_reason?: string | null }) | null;
  last_error: string | null;
}
export interface AffectedOutputs {
  schema_id: string;
  stale_outputs: { id: string; kind: string; created_at: string; changed_items: number }[];
  superseded_schema: {
    schema_id: string;
    version: number;
    diff: { added: string[]; removed: string[]; changed: string[] };
    evidence_items: number;
    outputs: number;
    rerun_fields: string[];
  } | null;
}
export interface FieldCoverage {
  field_key: string;
  label: string;
  included_papers: number;
  found: number;
  not_located: number;
  unavailable: number;
  ambiguous: number;
  pending: number;
}
export interface SchemaCoverage {
  schema_id: string;
  schema_version: number;
  included_papers: number;
  by_question: Record<string, { question_id: string; fields: FieldCoverage[] }>;
  unmapped_fields: FieldCoverage[];
}

// ---------- Phase 10: appraisal and synthesis ----------
export interface AppraisalFramework {
  key: string;
  version: string;
  label: string;
  kind: "risk_of_bias" | "checklist";
  designs: string[];
  based_on: string;
  reference_url: string;
  judgements: string[];
  overall_options: string[];
  overall_rule: string;
  domains: { key: string; label: string; prompt: string }[];
}
export interface AiAppraisalSuggestion {
  judgement: string;
  justification: string | null;
  excerpt: string | null;
  document_page_id: string | null;
  page_no: number | null;
  supported: boolean;
  model_run_id: string | null;
  prompt_version: string;
}
export interface AppraisalAnswer {
  judgement?: string;
  justification?: string;
  excerpt?: string | null;
  document_page_id?: string | null;
  page_no?: number | null;
  decided_by?: string;
  decided_at?: string;
  ai_suggestion?: AiAppraisalSuggestion | null;
}
export interface AppraisalRecord {
  id: string;
  paper_id: string;
  framework_key: string;
  framework_version: string;
  status: "draft" | "approved";
  answers: Record<string, AppraisalAnswer>;
  suggested_overall: string | null;
  overall_judgement: string | null;
  overall_justification: string | null;
  domains_total: number;
  domains_judged: number;
  ai_suggestions: number;
  approved_at: string | null;
  row_version: number;
}
export interface AppraisalPaper {
  paper: { id: string; title: string; year: number | null; publication_type: string | null };
  design_hint: string | null;
  suggested_framework: string | null;
  records: AppraisalRecord[];
}
export interface AppraisalOverview {
  frameworks: AppraisalFramework[];
  papers: AppraisalPaper[];
}
export type Certainty = "supported" | "uncertain" | "conflicting" | "insufficient";
export type StatementKind =
  "finding" | "agreement" | "disagreement" | "gap" | "limitation" | "method_difference" | "conclusion";
export interface SynthesisStatement {
  id: string;
  kind: StatementKind;
  text: string;
  group: string | null;
  citations: { evidence_item_id: string; revision: number; paper_id: string; field_key: string }[];
  appraisals: { paper_id: string; record_id: string; framework_key: string; overall: string | null }[];
  claimed_certainty: Certainty | null;
  certainty: Certainty;
  certainty_reasons: string[];
  origin: "system" | "ai" | "manual";
  review: "unreviewed" | "accepted" | "rejected" | "edited";
  warnings: ValidationWarning[];
  stale: boolean;
  stale_reasons: string[];
}
export interface SynthesisVersion {
  id: string;
  version: number;
  status: "draft" | "accepted" | "superseded";
  extraction_schema_id: string;
  scope: { group_by?: string | null };
  statements: SynthesisStatement[];
  review_counts: Record<string, number>;
  meta_analysis: { in_scope: boolean; note: string };
  accepted_at: string | null;
  accept_note: string | null;
  supersedes_id: string | null;
  row_version: number;
  created_at: string;
}
export interface FieldSignal {
  field_key: string;
  label: string;
  type: string;
  pattern: "no_evidence" | "single_study" | "agreement" | "disagreement" | "varied";
  studies_with_value: number;
  values: { value: string; paper_ids: string[]; item_ids: string[] }[];
  gaps: Record<"not_located" | "unavailable" | "ambiguous" | "not_reviewed", string[]>;
  range?: { min: number; max: number; n: number };
}
export interface ComparisonStudy {
  paper_id: string;
  title: string;
  year: number | null;
  appraisal: { record_id: string; framework_key: string; overall: string | null } | null;
  cells: Record<
    string,
    {
      item_id: string;
      status: EvidenceStatus;
      value: unknown;
      excerpt: string | null;
      page_no: number | null;
      review_status: string;
      reviewed: boolean;
    }
  >;
}
export interface Comparison {
  schema: { id: string; version: number; fields: FieldDefinition[] } | null;
  group_by?: string | null;
  groups: { key: string; label: string; paper_ids: string[]; fields: FieldSignal[] }[];
  studies: ComparisonStudy[];
  method_differences?: { designs: Record<string, string[]>; mixed_designs: boolean };
  limitations?: { serious_appraisal_concerns: string[]; not_appraised: string[] };
  unreviewed_values?: number;
  meta_analysis: { in_scope: boolean; note: string };
  group_options: { value: string; label: string }[];
}
export interface AiJobStatus {
  consent: ConsentStatus;
  active_job_id: string | null;
  last_result: (Record<string, unknown> & { stop_reason?: string | null; blocked_reason?: string }) | null;
  last_error: string | null;
}

// ---------- Phase 11: report drafts ----------
export type ReportBlock =
  | {
      id: string;
      type: "paragraph";
      text: string;
      origin: "generated" | "edited";
      source: string;
    }
  | {
      id: string;
      type: "statement";
      text: string;
      origin: "generated" | "edited";
      source: string;
      certainty: Certainty | null;
      kind?: StatementKind;
      evidence: { item_id: string; revision: number; paper_id: string; field_key: string }[];
    }
  | {
      id: string;
      type: "table";
      source: string;
      table: { key: string; caption: string; columns: string[]; rows: (string | number | null)[][] };
    }
  | { id: string; type: "references" };
export interface ReportSection {
  key: string;
  title: string;
  required: boolean;
  blocks: ReportBlock[];
  verified_by: string | null;
  verified_at: string | null;
}
export interface ReportIssue {
  code: string;
  severity: "error" | "warning";
  section: string;
  block_id: string | null;
  message: string;
}
export interface ReportSummary {
  errors: number;
  warnings: number;
  sections_total: number;
  sections_verified: number;
  label: string;
  publication_ready: false;
}
export interface ReportReference {
  key: string;
  paper_id: string;
  formatted: string;
  doi: string | null;
  pmid: string | null;
  year: number | null;
  title: string;
}
export interface ReportVersion {
  id: string;
  version: number;
  status: "current" | "superseded";
  title: string;
  sections: ReportSection[];
  references: ReportReference[];
  review_state: { fingerprint: string; generated_at: string; prisma: Record<string, unknown> };
  data_changed_since_generation: boolean;
  validation: { issues: ReportIssue[]; summary: ReportSummary };
  change_summary: string | null;
  supersedes_id: string | null;
  created_at: string;
}
export interface ReportVersionSummary {
  id: string;
  version: number;
  status: "current" | "superseded";
  title: string;
  change_summary: string | null;
  summary: ReportSummary | null;
  created_at: string;
}
export interface ReportExportRecord {
  id: string;
  format: string;
  filename: string;
  sha256: string;
  byte_size: number;
  validation_summary: ReportSummary;
  data_changed_since_draft: boolean;
  created_at: string;
}
export interface ClaimTrace {
  claim_id: string;
  source_explanation: string | null;
  facts: Record<string, unknown> | null;
  references: {
    key: string;
    paper_id: string;
    formatted: string;
    doi: string | null;
    pmid: string | null;
    is_retracted: boolean | null;
  }[];
  evidence: {
    evidence_item_id: string;
    paper_id: string;
    field_key: string;
    status: string;
    value: unknown;
    excerpt: string | null;
    page_no: number | null;
    changed_since_cited: boolean;
  }[];
}

// ---------- Phase 13: controlled autonomy ----------
export interface PipelinePending {
  type: "approval" | "wait_job" | "backoff";
  kind?: string;
  stage: string;
  key: string;
  message: string;
  options?: string[];
  job_id?: string;
  until?: string;
  ai?: boolean;
  detail?: Record<string, unknown>;
}
export interface PipelineRunView {
  id: string;
  status: "queued" | "running" | "waiting" | "paused" | "completed" | "cancelled" | "failed" | "superseded";
  stage: string | null;
  start_stage: string;
  autonomy: Record<string, boolean>;
  pending: PipelinePending | null;
  outputs: Record<string, Record<string, unknown>>;
  counters: { retries?: Record<string, number>; skipped?: string[]; jobs_dispatched?: number };
  supersedes_id: string | null;
  rerun_reason: string | null;
  error_summary: string | null;
  created_at: string;
  finished_at: string | null;
  changes_since_previous_run: Record<string, string[]>;
  stages: { key: string; label: string }[];
  events: {
    kind: string;
    stage: string | null;
    payload: Record<string, unknown>;
    at: string;
    by_person: boolean;
  }[];
}
export interface PipelineInvalidation {
  id: string;
  stage: string;
  cause: string;
  detail: Record<string, unknown>;
  status: "open" | "resolved" | "dismissed";
  resolution: string | null;
  created_at: string;
}
export interface PipelineOverview {
  run: PipelineRunView | null;
  history: {
    id: string;
    status: string;
    start_stage: string;
    rerun_reason: string | null;
    created_at: string;
  }[];
  invalidations: PipelineInvalidation[];
  defaults: { autonomy: Record<string, boolean>; stages: string[] };
  limits: Record<string, number>;
}
export interface CriteriaImpact {
  counts: { needs_redecision?: number; needs_review?: number; carry_forward?: number };
  criteria_changes: { added?: string[]; removed?: string[]; changed?: string[] };
  needs_redecision: { paper_id: string; title: string; why: string; decision: string }[];
  needs_review: { paper_id: string; title: string; why: string; decision: string }[];
  carry_forward: { paper_id: string; title: string; why: string; decision: string }[];
  protocol_version?: number;
}
