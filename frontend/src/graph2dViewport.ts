export interface GraphView {
  x: number;
  y: number;
  scale: number;
}

interface Point { x: number; y: number }
interface Size { width: number; height: number }

const finite = (value: number, fallback: number) => Number.isFinite(value) ? value : fallback;

function usableView(view: GraphView): GraphView {
  return {
    x: finite(view.x, 0),
    y: finite(view.y, 0),
    scale: Number.isFinite(view.scale) && view.scale > 0 ? view.scale : 1,
  };
}

export function zoomGraphView(view: GraphView, factor: number, anchor: Point): GraphView {
  const current = usableView(view);
  if (!Number.isFinite(factor) || factor <= 0) return current;
  const scale = Math.max(0.05, Math.min(1.8, current.scale * factor));
  const ratio = scale / current.scale;
  const anchorX = finite(anchor.x, 0);
  const anchorY = finite(anchor.y, 0);
  return {
    x: finite(anchorX - (anchorX - current.x) * ratio, current.x),
    y: finite(anchorY - (anchorY - current.y) * ratio, current.y),
    scale,
  };
}

export function fitGraphView(bounds: Size, viewport: Size): GraphView {
  const validSize = (size: Size) => Number.isFinite(size.width) && size.width > 0
    && Number.isFinite(size.height) && size.height > 0;
  if (!validSize(viewport)) return { x: 0, y: 0, scale: 1 };
  if (!validSize(bounds)) return { x: viewport.width / 2, y: viewport.height / 2, scale: 1 };
  const paddingX = Math.min(20, viewport.width / 4);
  const paddingY = Math.min(20, viewport.height / 4);
  const scale = Math.max(Number.MIN_VALUE, Math.min(1,
    (viewport.width - paddingX * 2) / bounds.width,
    (viewport.height - paddingY * 2) / bounds.height));
  return {
    x: (viewport.width - bounds.width * scale) / 2,
    y: (viewport.height - bounds.height * scale) / 2,
    scale,
  };
}

export function panGraphView(view: GraphView, delta: Point): GraphView {
  const current = usableView(view);
  return {
    x: finite(current.x + finite(delta.x, 0), current.x),
    y: finite(current.y + finite(delta.y, 0), current.y),
    scale: current.scale,
  };
}
