import { request } from './api';
import type { VideoProjectSummary } from './api';

/**
 * One shared /projects read for the pickers in the workbench, learning view and
 * task center. Switching pages must reuse the list instead of refetching it and
 * flashing a transient "loading" row that moves the page.
 *
 * `version` mirrors App's libraryVersion: renames, naming changes and new
 * archives bump it, which invalidates the cache.
 */
type Catalog = { version: number; projects: VideoProjectSummary[] };
let cached: Catalog | null = null;
let pending: { version: number; promise: Promise<VideoProjectSummary[]> } | null = null;
let sequence = 0;

const archived = (project: VideoProjectSummary) => project.archived === true;

export function cachedProjects(version: number): VideoProjectSummary[] | null {
  return cached?.version === version ? cached.projects : null;
}

/** `refresh` bypasses both the resolved cache and the de-duplicated in-flight read. */
export function loadProjects(version: number, refresh = false): Promise<VideoProjectSummary[]> {
  if (!refresh) {
    const ready = cachedProjects(version);
    if (ready) return Promise.resolve(ready);
    if (pending?.version === version) return pending.promise;
  }
  const id = ++sequence;
  const promise = request<{ projects: VideoProjectSummary[] }>('/projects', new AbortController().signal)
    .then(result => {
      const projects = result.projects.filter(archived);
      if (id === sequence) { cached = { version, projects }; pending = null; }
      return projects;
    }, (reason: unknown) => {
      if (id === sequence) pending = null;
      throw reason;
    });
  pending = { version, promise };
  return promise;
}
