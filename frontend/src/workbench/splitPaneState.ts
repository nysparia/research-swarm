/** Web split-view constraints, adapted from Apple's macOS Split views HIG.
 * Percentages are UI preferences only and never enter a research API payload.
 */
export const DEFAULT_PANE_PERCENT = 42;
export function paneLimits(width: number): [number, number] {
  if (!Number.isFinite(width) || width < 600) return [42, 42];
  return [Math.max(28, 28000 / width), Math.min(65, (width - 320) * 100 / width)];
}
export function clampPanePercent(value: number, width: number): number {
  const [min, max] = paneLimits(width);
  return Math.max(min, Math.min(max, Number.isFinite(value) ? value : DEFAULT_PANE_PERCENT));
}
export function panePercentAt(clientX: number, left: number, width: number): number {
  return clampPanePercent((clientX - left) / width * 100, width);
}
export function panePercentForKey(value: number, key: string, width: number, shift = false): number | null {
  const [min, max] = paneLimits(width);
  if (key === 'Home') return min;
  if (key === 'End') return max;
  if (key === 'Enter') return clampPanePercent(DEFAULT_PANE_PERCENT, width);
  if (key !== 'ArrowLeft' && key !== 'ArrowRight') return null;
  return clampPanePercent(value + (key === 'ArrowLeft' ? -1 : 1) * (shift ? 5 : 2), width);
}
