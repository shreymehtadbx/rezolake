// "Why are these together?" as a graph: records are nodes, decisions and human constraints are edges.
// Rendered with ECharts' force-directed graph (the engine behind AppKit's charts), themed from the
// same CSS tokens AppKit's charts read.
import { useMemo } from 'react';
import { useChartUITokens, useThemeColors } from '@databricks/appkit-ui/react';
import ReactEChartsCore from 'echarts-for-react/esm/core';
import * as echarts from 'echarts/core';
import { GraphChart } from 'echarts/charts';
import { TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([GraphChart, TooltipComponent, CanvasRenderer]);

export interface GraphNode {
  id: string;
  label: string;
  source: string;
  anchor?: boolean;
  /** Records outside this entity (shown when non-matches are included). */
  external?: boolean;
  /** Suggested-split group index, if a suggestion is active. */
  group?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  /** LINK | REVIEW | NO_LINK | MUST_LINK | CANNOT_LINK */
  label: string;
  score: number | null;
  /** Edge is cut by the active split suggestion. */
  cut?: boolean;
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function ClusterGraph({ nodes, edges, selected, onSelect, height = 380 }: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected?: string;
  onSelect?: (recordId: string) => void;
  height?: number;
}) {
  const ui = useChartUITokens(); // also re-renders on theme change, so the token reads below refresh
  const categorical = useThemeColors('categorical');
  const option = useMemo(() => {
    // Ordinary links are neutral; colour is reserved for what needs attention.
    const tone = {
      LINK: cssVar('--muted-foreground', '#666'),
      MUST_LINK: cssVar('--primary', '#1565c0'),
      REVIEW: cssVar('--warning', '#ed6c02'),
      NO_LINK: cssVar('--destructive', '#c62828'),
      CANNOT_LINK: cssVar('--destructive', '#c62828'),
    } as Record<string, string>;
    const muted = cssVar('--muted-foreground', '#888');
    const sources = [...new Set(nodes.map((n) => n.source))].sort();
    const grouped = nodes.some((n) => n.group !== undefined);
    const colorOf = (n: GraphNode) =>
      n.external ? muted : categorical[(grouped ? n.group ?? 0 : sources.indexOf(n.source)) % categorical.length];
    return {
      tooltip: {
        backgroundColor: ui.tooltipBg,
        textStyle: { color: ui.axisTitle },
        formatter: (p: { dataType: string; data: { name?: string; label?: string; score?: number | null; kind?: string } }) =>
          p.dataType === 'edge'
            ? `${p.data.kind}${p.data.score == null ? '' : ` · ${p.data.score.toFixed(2)}`}`
            : p.data.name ?? '',
      },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        force: { repulsion: 220, edgeLength: [60, 160], gravity: 0.08 },
        label: { show: nodes.length <= 12, position: 'right', color: ui.axisLabel, fontSize: 10, formatter: '{b}' },
        emphasis: { focus: 'adjacency', label: { show: true }, lineStyle: { width: 4 } },
        data: nodes.map((n) => ({
          id: n.id,
          name: n.label,
          symbolSize: n.anchor ? 22 : 15,
          symbol: n.external ? 'diamond' : 'circle',
          itemStyle: {
            color: colorOf(n),
            borderColor: n.id === selected ? ui.axisTitle : 'transparent',
            borderWidth: n.id === selected ? 3 : 0,
            opacity: n.external ? 0.6 : 1,
          },
        })),
        links: edges.map((e) => ({
          source: e.source,
          target: e.target,
          kind: e.label.replace(/_/g, ' '),
          score: e.score,
          lineStyle: {
            color: tone[e.label] ?? muted,
            width: e.label === 'MUST_LINK' ? 3 : 1 + 3 * Math.max(0, Math.min(1, e.score ?? 0.5)),
            type: e.cut || e.label === 'NO_LINK' || e.label === 'CANNOT_LINK' ? 'dashed' : 'solid',
            opacity: e.cut ? 0.35 : e.label === 'NO_LINK' ? 0.45 : 0.85,
            curveness: 0.05,
          },
        })),
      }],
    };
  }, [nodes, edges, selected, ui, categorical]);

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      style={{ height, width: '100%' }}
      onEvents={{
        click: (p: { dataType?: string; data?: { id?: string } }) => {
          if (p.dataType === 'node' && p.data?.id) onSelect?.(p.data.id);
        },
      }}
    />
  );
}
