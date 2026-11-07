import type { TaskInfo } from '../../lib/jobs';
export const templates: Record<string, string> = { summary: '内容摘要', chapters: '章节时间轴', notes: '学习笔记', breakdown: '短视频拆解', storyboard: '分镜表', script: '提取口播文案', custom: '自定义', conversation: 'AI 对话' };
export const sources: Record<string, string> = { both: '字幕＋画面', transcript: '只用字幕', frames: '只用画面' };
export const engines: Record<string, string> = { api: 'API', claude: 'Claude Code', codex: 'Codex', opencode: 'OpenCode', dsh: 'DeepSeek Harness' };
export const modes: Record<string, string> = { scene: '镜头检测＋均匀补足', uniform: '时间均分', ai: 'AI 字幕选帧' };
export function taskDescription(job: TaskInfo) {
  const d = job.details;
  return d ? [templates[d.template || ''] || d.template, engines[d.engine || ''] || d.engine, d.model || (d.engine ? '跟随工具默认模型' : ''), sources[d.source || ''], modes[d.frame_mode || ''], d.count ? `${d.count} 张` : ''].filter(Boolean).join(' · ') : '';
}
