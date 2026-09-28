import type { VisualLink, VisualNode } from './graphData';

export interface PositionedNode extends VisualNode {
  x: number;
  y: number;
  width: number;
  height: number;
  depth: number;
}

/** Manual world coordinates stay fixed even when new branches rearrange the automatic layout. */
export function applyManualNodePositions<T extends PositionedNode>(
  nodes: readonly T[],
  positions: Readonly<Record<string, { x: number; y: number }>>,
): T[] {
  return nodes.map(node => {
    const position = positions[node.id];
    return position ? { ...node, x: position.x, y: position.y } : node;
  });
}

const CARD_WIDTH = 240;
const CARD_HEIGHT = 112;
const SIBLING_GAP = 32;
const LAYER_GAP = 72;
const MARGIN = 32;

/** A stable forest layout; only the returned card coordinates change, never graph relationships. */
export function layoutResearchGraph(
  nodes: VisualNode[],
  links: VisualLink[],
): { nodes: PositionedNode[]; width: number; height: number } {
  if (!nodes.length) return { nodes: [], width: 0, height: 0 };

  const indices = nodes.map((_, index) => index);
  const byId = new Map<string, number>();
  for (const index of indices) if (!byId.has(nodes[index].id)) byId.set(nodes[index].id, index);
  const compare = (a: number, b: number) => {
    if (nodes[a].id === 'central' && nodes[b].id !== 'central') return -1;
    if (nodes[b].id === 'central' && nodes[a].id !== 'central') return 1;
    return nodes[a].id < nodes[b].id ? -1 : nodes[a].id > nodes[b].id ? 1 : a - b;
  };
  const ordered = [...indices].sort(compare);
  const endpoint = (value: VisualLink['source']) => (typeof value === 'string' ? value : value.id);
  const inferredParents = new Map<number, number[]>();
  for (const link of links) {
    if (link.type !== 'decompose' && link.type !== 'facet') continue;
    const source = byId.get(endpoint(link.source));
    const target = byId.get(endpoint(link.target));
    if (source === undefined || target === undefined || source === target) continue;
    const candidates = inferredParents.get(target) || [];
    candidates.push(source);
    inferredParents.set(target, candidates);
  }
  const parents = nodes.map((node, index): number | null => {
    const actual = node.parentId == null ? undefined : byId.get(node.parentId);
    if (actual !== undefined) return actual;
    const candidates = inferredParents.get(index);
    return candidates?.length ? [...candidates].sort(compare)[0] : null;
  });

  // A cyclic graph cannot satisfy every downward relation. Break one layout parent
  // per cycle deterministically, preserving the original nodes and links unchanged.
  const visited = new Set<number>();
  for (const start of ordered) {
    if (visited.has(start)) continue;
    const path: number[] = [];
    const onPath = new Map<number, number>();
    let current: number | null = start;
    while (current !== null && !visited.has(current)) {
      const cycleStart = onPath.get(current);
      if (cycleStart !== undefined) {
        const root = path.slice(cycleStart).sort(compare)[0];
        parents[root] = null;
        break;
      }
      onPath.set(current, path.length);
      path.push(current);
      current = parents[current];
    }
    for (const index of path) visited.add(index);
  }

  const children = nodes.map(() => [] as number[]);
  const roots: number[] = [];
  for (const index of ordered) {
    const parent = parents[index];
    if (parent === null) roots.push(index);
    else children[parent].push(index);
  }

  // Iterative traversals keep very deep decomposition chains safe from stack limits.
  const traversal: number[] = [];
  const stack = [...roots];
  while (stack.length) {
    const index = stack.pop()!;
    traversal.push(index);
    for (const child of children[index]) stack.push(child);
  }
  const subtreeWidths = nodes.map(() => CARD_WIDTH);
  for (let order = traversal.length - 1; order >= 0; order--) {
    const index = traversal[order];
    if (children[index].length) {
      const width =
        children[index].reduce((total, child) => total + subtreeWidths[child], 0) +
        SIBLING_GAP * (children[index].length - 1);
      subtreeWidths[index] = Math.max(CARD_WIDTH, width);
    }
  }

  const positioned = new Array<PositionedNode>(nodes.length);
  const pending: { index: number; left: number; depth: number }[] = [];
  let rootLeft = MARGIN;
  for (const root of roots) {
    pending.push({ index: root, left: rootLeft, depth: 0 });
    rootLeft += subtreeWidths[root] + SIBLING_GAP;
  }
  let deepest = 0;
  while (pending.length) {
    const { index, left, depth } = pending.pop()!;
    deepest = Math.max(deepest, depth);
    positioned[index] = {
      ...nodes[index],
      x: left + (subtreeWidths[index] - CARD_WIDTH) / 2,
      y: MARGIN + depth * (CARD_HEIGHT + LAYER_GAP),
      width: CARD_WIDTH,
      height: CARD_HEIGHT,
      depth,
    };
    let childLeft = left;
    for (const child of children[index]) {
      pending.push({ index: child, left: childLeft, depth: depth + 1 });
      childLeft += subtreeWidths[child] + SIBLING_GAP;
    }
  }
  return {
    nodes: positioned,
    width: rootLeft - SIBLING_GAP + MARGIN,
    height: MARGIN * 2 + CARD_HEIGHT + deepest * (CARD_HEIGHT + LAYER_GAP),
  };
}
