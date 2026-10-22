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
