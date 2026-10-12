// Split suggestions for an entity: keep the strongest links that still connect the records (a
// maximum spanning tree over the entity's internal LINK / MUST_LINK edges), then cut the weakest
// tree links. Records left disconnected become the suggested groups. Human MUST_LINKs never cut.

export interface SplitEdge {
  left: string;
  right: string;
  /** P(match) for automatic links; ignored for MUST_LINK. */
  score: number;
  kind: 'LINK' | 'MUST_LINK';
}

export interface SplitSuggestion {
  /** Record groups, largest first; groups[0] keeps the entity id (it holds the anchor when given). */
  groups: string[][];
  /** Tree links the suggestion cuts, weakest first. */
  cut: SplitEdge[];
}

const weight = (e: SplitEdge) => (e.kind === 'MUST_LINK' ? Number.POSITIVE_INFINITY : e.score);

function find(parent: Map<string, string>, x: string): string {
  let r = x;
  while (parent.get(r) !== r) r = parent.get(r)!;
  while (parent.get(x) !== r) {
    const next = parent.get(x)!;
    parent.set(x, r);
    x = next;
  }
  return r;
}

function components(nodes: string[], edges: SplitEdge[]): string[][] {
  const parent = new Map(nodes.map((n) => [n, n]));
  for (const e of edges) {
    if (!parent.has(e.left) || !parent.has(e.right)) continue;
    parent.set(find(parent, e.left), find(parent, e.right));
  }
  const groups = new Map<string, string[]>();
  for (const n of nodes) {
    const r = find(parent, n);
    groups.set(r, [...(groups.get(r) ?? []), n]);
  }
  return [...groups.values()];
}

/** Maximum spanning forest (Kruskal on descending weight). */
export function spanningTree(nodes: string[], edges: SplitEdge[]): SplitEdge[] {
  const parent = new Map(nodes.map((n) => [n, n]));
  const tree: SplitEdge[] = [];
  const sorted = [...edges]
    .filter((e) => parent.has(e.left) && parent.has(e.right) && e.left !== e.right)
    .sort((a, b) => weight(b) - weight(a) || `${a.left}${a.right}`.localeCompare(`${b.left}${b.right}`));
  for (const e of sorted) {
    const a = find(parent, e.left);
    const b = find(parent, e.right);
    if (a === b) continue;
    parent.set(a, b);
    tree.push(e);
  }
  return tree;
}

/**
 * Suggest how to split an entity.
 * @param cutBelow cut every tree link scoring below this; omit to cut only the single weakest link.
 * @param anchor record that should stay in the surviving group (groups[0]).
 * Returns null when there is nothing to cut (fewer than 2 records, or every tree link is a MUST_LINK
 * or at/above `cutBelow`).
 */
export function suggestSplit(nodes: string[], edges: SplitEdge[], cutBelow?: number, anchor?: string): SplitSuggestion | null {
  if (nodes.length < 2) return null;
  const tree = spanningTree(nodes, edges);
  const cuttable = tree.filter((e) => e.kind !== 'MUST_LINK').sort((a, b) => a.score - b.score);
  const cut = cutBelow === undefined ? cuttable.slice(0, 1) : cuttable.filter((e) => e.score < cutBelow);
  const alreadyApart = components(nodes, tree).length > 1;
  if (!cut.length && !alreadyApart) return null;
  const kept = tree.filter((e) => !cut.includes(e));
  const groups = components(nodes, kept).sort((a, b) => b.length - a.length);
  if (anchor) {
    const i = groups.findIndex((g) => g.includes(anchor));
    if (i > 0) groups.unshift(...groups.splice(i, 1));
  }
  return { groups, cut };
}
