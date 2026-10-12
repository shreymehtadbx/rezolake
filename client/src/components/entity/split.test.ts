import { describe, expect, it } from 'vitest';
import { spanningTree, suggestSplit, type SplitEdge } from './split';

const link = (left: string, right: string, score: number): SplitEdge => ({ left, right, score, kind: 'LINK' });

describe('suggestSplit', () => {
  // Two tight triangles joined by one weak link: c–d.
  const nodes = ['a', 'b', 'c', 'd', 'e', 'f'];
  const edges = [
    link('a', 'b', 0.99), link('b', 'c', 0.97), link('a', 'c', 0.95),
    link('d', 'e', 0.98), link('e', 'f', 0.96), link('d', 'f', 0.94),
    link('c', 'd', 0.86),
  ];

  it('keeps a spanning tree of the strongest links', () => {
    const tree = spanningTree(nodes, edges);
    expect(tree).toHaveLength(5);
    expect(tree.map((e) => e.score)).not.toContain(0.95);
  });

  it('cuts the single weakest link by default', () => {
    const s = suggestSplit(nodes, edges)!;
    expect(s.cut).toEqual([link('c', 'd', 0.86)]);
    expect(s.groups.map((g) => [...g].sort())).toEqual([['a', 'b', 'c'], ['d', 'e', 'f']]);
  });

  it('cuts every link below a threshold and keeps the anchor group first', () => {
    const s = suggestSplit(nodes, edges, 0.965, 'f')!;
    expect(s.cut.map((e) => e.score)).toEqual([0.86, 0.96]);
    expect(s.groups[0]).toContain('f');
    expect(s.groups).toHaveLength(3);
  });

  it('never cuts a human MUST_LINK', () => {
    const s = suggestSplit(['a', 'b'], [{ left: 'a', right: 'b', score: 0, kind: 'MUST_LINK' }], 0.99);
    expect(s).toBeNull();
  });

  it('reports records with no internal link as their own group', () => {
    const s = suggestSplit(['a', 'b', 'c'], [link('a', 'b', 0.99)], 0.5)!;
    expect(s.cut).toEqual([]);
    expect(s.groups).toEqual([['a', 'b'], ['c']]);
  });

  it('returns null for a single record', () => {
    expect(suggestSplit(['a'], [])).toBeNull();
  });
});
