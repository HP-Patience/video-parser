export interface UpdateStatus {
  current_version: string;
  latest_version: string | null;
  release_found: boolean;
  available: boolean;
  can_install: boolean;
  install_reason: 'installed' | 'not_installed' | 'assets_missing' | 'up_to_date' | 'no_release';
  release_url: string;
  published_at?: string | null;
}

export interface UpdateInstallResult { started: boolean; version: string; installer: string }

export interface Configuration {
  platforms: string[];
  ai_configured: boolean;
  media_ready: boolean;
  subtitles_ready: boolean;
  transcription_ready: boolean;
  transcription_upload_limit: number;
  archive_root: string;
  version?: string;
  ai?: AISettings;
}

export interface AIPreset { name: string; base_url: string; model: string; key_required: boolean; models: string[]; hint: string }
export interface AILaunchSettings {
  server_path: string; model_path: string; vision_path: string; port: number;
  context_size: number; gpu_layers: number; threads: number; parallel: number;
  show_console: boolean; flash_attention: boolean; image_max_tokens: number; max_output_tokens: number;
}
export interface AIRuntimeStatus {
  state: 'unconfigured' | 'stopped' | 'starting' | 'running' | 'failed' | 'occupied';
  message: string; owned: boolean; elapsed_seconds: number; model?: string; base_url?: string;
}
export interface AISettings {
  provider: string; provider_name: string; base_url: string; model: string;
  key_saved: boolean; key_hint: string; price_input: number | null; price_output: number | null;
  configured: boolean; local: boolean; consented: boolean; source: 'settings' | 'env' | 'none'; storage: string;
  presets: Record<string, AIPreset>;
  startup?: AILaunchSettings; startup_defaults?: AILaunchSettings; startup_command?: string; startup_error?: string;
}
export type AgentEngine = 'api' | 'claude' | 'codex' | 'opencode';
export interface AgentTarget { engine: AgentEngine; model: string; effort: string }
export interface AgentModel { id: string; label: string; efforts: string[]; images: boolean | null }
export interface AgentCommand { name: string; prefix: string; description: string }
export interface AgentCatalog {
  engines: { id: Exclude<AgentEngine, 'api'>; name: string; installed: boolean; enabled?: boolean; discovered: boolean;
    models: AgentModel[]; commands: AgentCommand[]; error: string; updated_at?: number | null }[];
  permission: string;
}
export type AnalysisSource = 'both' | 'transcript' | 'frames';
export interface AnalysisTemplate { id: string; name: string; description: string; source: AnalysisSource; requires: 'frames' | 'transcript' | null }
export interface TranscriptOption { resource: string; label: string; kind: string; language: string; cues: number; chars: number; merged: boolean; tokens: number; end: string }
export interface AnalysisContext {
  transcripts: TranscriptOption[]; duration: number; auto_frame_count: number; image_tokens: number;
  prompt_tokens: number; output_tokens: number; templates: AnalysisTemplate[]; sources: Record<AnalysisSource, string>; ai: AISettings;
}
export interface AnalysisFrame { url: string; time: number; label: string; name?: string }
export interface QuizQuestion {
  question: string; options: string[]; answer: number; answer_text: string; explanation: string; time: number | null;
}
export interface Quiz { title: string; questions: QuizQuestion[] }
/** Answers the learner already gave for one analysis record; keys are question indexes. */
export interface QuizState { answers: Record<string, number>; updated_at: string | null }
export interface AnalysisRecord {
  id: string; created_at: string | null; template: string; template_name: string; source: AnalysisSource; source_label: string;
  prompt: string; model: string; provider: string; engine?: AgentEngine; transcript_label: string; transcript_merged: boolean; video_resource: string | null;
  frame_count: number; frames: AnalysisFrame[]; usage: { prompt_tokens?: number | null; completion_tokens?: number | null };
  /** Parsed structure for the `quiz` template; null when the model output could not be validated. */
  quiz?: Quiz | null;
  text: string; followups: { id: string; question: string; answer: string; created_at: string | null }[];
  /** Last follow-up before the user cleared the chat context; later questions resend only what follows it. */
  context_after?: string | null; legacy: boolean;
}
export interface AnalysisBody {
  agent?: AgentTarget;
  template: string; source: AnalysisSource; transcript_resource: string | null; frame_mode: 'scene' | 'uniform' | 'ai';
  count: number; preview_token: string | null; frames: string[] | null; prompt: string;
}
export interface AnalysisResult { text: string; record?: AnalysisRecord; conversation?: Conversation; mode: 'analyze' | 'ask' | 'conversation'; project: VideoProject }

export interface ConversationMessage {
  id: string; question: string; answer: string; created_at: string | null; position?: number;
  images?: { name: string; mime?: string; size?: number }[];
  context?: { frame_times?: number[]; subtitle_ranges?: number[][]; warning?: string };
}
export interface Conversation {
  agent?: AgentTarget;
  id: string; title: string; title_generated: boolean; created_at: string | null; updated_at: string | null;
  origin_record?: string | null; context_after?: string | null; video_resource: string | null; messages: ConversationMessage[];
}
export interface ConversationContext { available: boolean; missing: string[] }

export interface ParsedVideo {
  id: string;
  title: string;
  platform: string;
  source_url: string;
  cover_url: string | null;
  cover_download_url: string | null;
  warning: string | null;
  project: VideoProject;
  media: Media | null;
  qualities: VideoQuality[];
  default_quality_id: string | null;
  bilibili_cookie_status?: string | null;
  duration?: number | null;
  episodes?: LearningEpisode[];
}

export interface VideoQuality {
  id: string;
  label: string;
  width: number | null;
  height: number | null;
  codec?: string | null;
  media: Media | null;
}

export interface Media {
  resource: string;
  quality_id: string;
  preview_url: string;
  download_url: string;
  filename: string;
  size_bytes: number;
  duration: number;
  width: number | null;
  height: number | null;
}

export interface LearningEpisode {
  part: number;
  title: string;
  duration: number | null;
  project_id: string | null;
  archived: boolean;
  learned: boolean;
}

export async function request<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const uploading = body instanceof File;
  const response = await fetch(`/api/workspace${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': uploading ? 'application/octet-stream' : 'application/json' },
    body: body === undefined ? undefined : uploading ? body : JSON.stringify(body),
    signal,
  }).catch((error: unknown) => {
    if (signal.aborted) throw error;
    throw new Error('无法连接工作台服务，请确认本地后端正在运行。');
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) {
    throw new Error(typeof data?.detail === 'string' ? data.detail :
      response.status === 422 ? '输入不符合要求，请检查填写内容与隐私确认。' : `服务处理失败（HTTP ${response.status}，请求 ${response.headers.get('X-Request-ID')?.slice(0,12) || 'unknown'}）。请检查任务中心或本机诊断。`);
  }
  return data as T;
}

export interface SubtitleJob {
  id: string;
  state: 'queued' | 'running' | 'cancelling' | 'completed' | 'partial' | 'failed' | 'login_required' | 'cancelled' | 'interrupted';
  message: string;
  title: string;
  downloaded: number;
  existing: number;
  failed: number;
  /** Episodes not enqueued because verified platform subtitles were already archived locally. */
  skipped?: number[];
  results: {
    episode: number;
    status: 'downloaded' | 'existing' | 'failed';
    message: string;
    lines?: number;
    files: { name: string; format: string; url: string }[];
    project?: VideoProject;
  }[];
}

export interface SubtitleInventory {
  video_id: string | null; total: number | null; archived: number[]; available?: number[];
  episodes: { part: number; title: string; subtitles: boolean }[];
}

/** One archived AI report; duplicate templates are intentionally kept as separate material rows. */
export interface VideoMaterialSummary {
  id: string;
  name: string;
  template: string;
  created_at: string;
}

/** Lightweight /projects rows for the all-video status overview. */
export interface VideoProjectSummary {
  id: string;
  title: string;
  platform: string;
  part: number | null;
  location: string;
  created_at: string;
  video_id?: string;
  episode_title?: string;
  display_title?: string;
  is_single?: boolean;
  archived: boolean;
  learned?: boolean;
  has_subtitles?: boolean;
  subtitle_count?: number;
  translation_count?: number;
  analysis_count?: number;
  materials?: VideoMaterialSummary[];
}

export interface VideoProject {
  id: string;
  video_id?: string;
  episode_title?: string;
  display_title?: string;
  is_single?: boolean;
  title: string;
  platform: string;
  part: number | null;
  location: string;
  identity_status: string;
  learned?: boolean;
  archive_url: string;
  resources: { path: string; kind: string; size_bytes: number; url: string; filename?: string; storage_path?: string }[];
}

export interface TranscriptionSource {
  project_id: string;
  resource: string;
  title: string;
  platform: string;
  source_url?: string;
  part: number | null;
  quality: string;
  duration_seconds: number | null;
  size_bytes: number;
  archived_at: string;
  has_subtitles?: boolean;
  subtitle_count?: number;
}

export interface ArchivedTranslation { location?: string; resource: string; filename: string; language: string; text: string; url: string }

export interface SubtitleVersion { version_id?: string; generated_at?: number | null; format?: string; source_resolution?: string; resource: string; filename: string; kind?: string; origin?: 'platform' | 'local' | 'translation' | 'corrected' | 'unknown'; url: string; language: string; sha256?: string; source_resource?: string; source_origin?: string; source_language?: string }

export interface ArchivedSubtitles {
  versions?: SubtitleVersion[];
  translations?: ArchivedTranslation[];
  project_id: string; has_subtitles: boolean; text: string;
  files: SubtitleVersion[];
  selected: SubtitleVersion | null;
}

export interface TranscriptionJob {
  created?: number;
  operation?: 'transcription' | 'translation';
  translation_target?: 'zh' | 'en';
  translation_source?: string | null;
  translation_source_sha256?: string | null;
  id: string;
  state: 'queued' | 'running' | 'cancelling' | 'completed' | 'empty' | 'failed' | 'cancelled' | 'interrupted';
  message: string;
  title: string;
  text: string;
  language: string;
  files: { name: string; format: string; url: string; sha256?: string; resource?: string; project_id?: string }[];
  requested_language?: 'auto' | 'zh' | 'en';
  source_project_id?: string | null;
  source_resource?: string | null;
  saved_to?: { type: 'project' | 'folder' | 'default'; location: string; project_id?: string; title?: string };
  /** While transcribing: done/total are seconds of audio, eta_seconds is measured from the actual speed. */
  phase?: string;
  progress?: { done?: number; total?: number; eta_seconds?: number; sampled_at?: number };
}

export interface ProjectSummary {
  id: string;
  title: string;
  platform: string;
  part: number | null;
  location: string;
  created_at: string;
}

export interface UsageBucket {
  calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number;
  /** null when at least one API call has no configured price; 0 for native-agent calls. */
  cost: number | null; unpriced_calls: number; native_calls: number;
}
export interface UsageDay { date: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }
export interface UsageReport {
  generated_at: string; window_days: number; currency: string;
  prices: { input: number | null; output: number | null; configured: boolean };
  totals: UsageBucket & { projects: number };
  by_kind: (UsageBucket & { kind: string; label: string })[];
  by_model: (UsageBucket & { model: string; engines: string[] })[];
  by_project: (UsageBucket & { id: string; title: string })[];
  by_day: UsageDay[];
  coverage: string;
}

export interface ArchiveHealthIssue {
  project_id: string; title: string; resource?: string; path: string; kind?: string | null;
  recorded_bytes?: number | null; actual_bytes?: number; size_bytes?: number; suspected_move?: string; reason?: string; error?: string;
}
export interface ArchiveHealthListing<T> { items: T[]; total: number; shown: number }
export interface ArchiveHealthProject {
  id: string; title: string; platform: string; location: string; files: number; disk_bytes: number;
  resource_bytes: number; orphan_bytes: number; sidecar_bytes: number; missing: number; changed: number; orphans: number; issues: number;
}
export interface ArchiveHealth {
  generated_at: string; duration_ms: number; archive_root: string; read_only: boolean;
  totals: { projects: number; files: number; disk_bytes: number; resource_bytes: number; orphan_bytes: number; sidecar_bytes: number;
    missing: number; changed: number; orphan_files: number; unsafe: number; duplicate_groups: number; duplicate_bytes: number; unreadable: number };
  projects: ArchiveHealthProject[];
  issues: {
    missing: ArchiveHealthListing<ArchiveHealthIssue>; changed: ArchiveHealthListing<ArchiveHealthIssue>;
    orphans: ArchiveHealthListing<ArchiveHealthIssue>; unsafe: ArchiveHealthListing<ArchiveHealthIssue>;
    unreadable: ArchiveHealthListing<ArchiveHealthIssue>;
    duplicates: ArchiveHealthListing<{ sha256: string; size_bytes: number; copies?: number; entries: ArchiveHealthIssue[] }>;
  };
}
