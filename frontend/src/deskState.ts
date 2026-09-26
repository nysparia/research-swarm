import type { TaskDetail, ResearchArtifact } from './taskTypes';
import type { PaperSection } from './paperTypes';

export interface MeasurementChart { id: string; title: string; kind: 'bar' | 'line'; xLabel: string; yLabel: string; note: string; series: { name: string; points: { x: string | number; y: number }[] }[] }
export interface ArtifactPreview { name: string; path?: string; sha256?: string; bytes?: number; kind: string; text?: string; truncated?: boolean; warning?: string; dataUrl?: string; columns?: string[]; rows?: string[][]; rowCount?: number; dashboard?: { charts: MeasurementChart[] }; pending?: boolean; provisional?: boolean }

export function focusArtifacts(artifacts: ResearchArtifact[], nodeId: string | null) {
  return artifacts.filter(artifact => artifact.kind === 'experiment' && (!nodeId || artifact.nodeId === nodeId));
}

export function linkedSections(sections: PaperSection[], nodeId: string | null, evidenceIds: string[]) {
  if (!nodeId) return sections.filter(section => section.markdown || section.suggestion);
  return sections.filter(section => section.sourceNodeIds.includes(nodeId) || section.evidenceIds.some(id => evidenceIds.includes(id)) || section.suggestion?.sourceNodeIds.includes(nodeId));
}

export function artifactCurrent(artifact: ResearchArtifact, detail: TaskDetail) {
  const node = detail.state?.nodes.find(node => node.id === artifact.nodeId);
  return Boolean(artifact.valid && node?.active && node.version === artifact.nodeVersion && artifact.round === detail.state?.project.round);
}

export function changedLines(previous: string, next: string) {
  const a = previous.split('\n'), b = next.split('\n');
  let prefix = 0, suffix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
  while (suffix < a.length - prefix && suffix < b.length - prefix && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
  return new Set(b.flatMap((_, index) => index >= prefix && index < b.length - suffix ? [index] : []));
}

export function chartGeometry(chart: MeasurementChart) {
  const points = chart.series.flatMap(series => series.points);
  const min = Math.min(0, ...points.map(point => point.y));
  const max = Math.max(0, ...points.map(point => point.y));
  const numericX = points.every(point => typeof point.x === 'number');
  const categories = [...new Set(points.map(point => String(point.x)))];
  const xMin = numericX ? Math.min(...points.map(point => Number(point.x))) : 0;
  const xMax = numericX ? Math.max(...points.map(point => Number(point.x))) : categories.length - 1;
  return { min, max: max === min ? min + 1 : max, categories, numericX, xMin, xMax: xMax === xMin ? xMin + 1 : xMax };
}
