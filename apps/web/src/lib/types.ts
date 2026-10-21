export interface User { id: string; email: string; created_at: string }
export interface Project {
  id: string; title: string; topic_original: string; status: string; row_version: number;
  archived_at: string | null; created_at: string; updated_at: string;
}
export interface ProjectList { items: Project[]; total: number; limit: number; offset: number }
