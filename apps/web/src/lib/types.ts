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
