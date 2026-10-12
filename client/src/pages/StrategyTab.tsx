/* eslint-disable @typescript-eslint/no-unsafe-assignment, @typescript-eslint/no-unsafe-member-access -- untyped strategy JSON document */
import { useState } from 'react';
import { useOutletContext } from 'react-router';
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@databricks/appkit-ui/react';
import { api } from '../lib/api';
import type { EntityTypeOutlet } from './EntityTypeLayout';

const BLOCKERS = {
  nearest_by_blocker: 'NEAREST BY join over the embeddings Delta table (no index)',
  ai_search_blocker: 'AI Search ANN index (maintains an index; needs an AI Search endpoint)',
} as const;
const MATCHERS = {
  llm_entity_matcher: 'LLM EntityMatcher (explains each decision)',
  ai_decide_matcher: 'ai_decide decision model (Beta; no rationale)',
} as const;
type BlockerId = keyof typeof BLOCKERS;
type MatcherId = keyof typeof MATCHERS;

interface StrategyForm {
  embedding_endpoint: string;
  blocker: BlockerId;
  num_results: number;
  score_threshold: number;
  search: string;
  matcher: MatcherId;
  llm_endpoint: string;
  concurrency: number;
  link_threshold: number;
  review_threshold: number;
  /** 0 = off. A NO_LINK scored below this keeps two records apart even when links chain them together. */
  no_link_veto_below: number;
}

type Doc = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const DEFAULT_CONCURRENCY: Record<MatcherId, number> = { ai_decide_matcher: 32, llm_entity_matcher: 16 };

function fromDoc(d: Doc): StrategyForm {
  const blocker: BlockerId = d.blocker?.component in BLOCKERS ? d.blocker.component : 'nearest_by_blocker';
  const matcher: MatcherId = d.matchers?.[0]?.component in MATCHERS ? d.matchers[0].component : 'llm_entity_matcher';
  return {
    embedding_endpoint: d.representation?.embedding_endpoint ?? 'databricks-bge-large-en',
    blocker,
    num_results: d.blocker?.config?.num_results ?? 10,
    score_threshold: d.blocker?.config?.score_threshold ?? 0.5,
    search: d.blocker?.config?.search ?? 'exact',
    matcher,
    llm_endpoint: d.matchers?.[0]?.config?.llm_endpoint ?? 'databricks-claude-sonnet-4-6',
    concurrency: d.matchers?.[0]?.config?.concurrency ?? DEFAULT_CONCURRENCY[matcher],
    link_threshold: d.decision?.config?.link_threshold ?? 0.85,
    review_threshold: d.decision?.config?.review_threshold ?? 0.5,
    // New strategies guard against chained merges; an existing clusterer without the setting stays off.
    no_link_veto_below: d.clusterer?.config?.no_link_veto_below ?? (d.clusterer ? 0 : 0.2),
  };
}

function toDoc(f: StrategyForm): Doc {
  const blockerConfig =
    f.blocker === 'nearest_by_blocker'
      ? { num_results: f.num_results, score_threshold: f.score_threshold, search: f.search }
      : { num_results: f.num_results, score_threshold: f.score_threshold, concurrency: 16 };
  const matcherConfig =
    f.matcher === 'llm_entity_matcher' ? { llm_endpoint: f.llm_endpoint, concurrency: f.concurrency } : { concurrency: f.concurrency };
  return {
    representation: { embedding_endpoint: f.embedding_endpoint },
    blocker: { component: f.blocker, version: '1', config: blockerConfig },
    matchers: [{ component: f.matcher, version: '1', config: matcherConfig }],
    decision: { component: 'threshold_policy', version: '1', config: { link_threshold: f.link_threshold, review_threshold: f.review_threshold, combine: 'max' } },
    clusterer: f.no_link_veto_below > 0
      ? { component: 'constrained_components', version: '2', config: { no_link_veto_below: f.no_link_veto_below } }
      : { component: 'constrained_components', version: '1', config: {} },
    survivorship: { component: 'attribute_rules', version: '1', config: {} },
  };
}

function ComponentSelect<T extends string>({ value, options, onChange }: { value: T; options: Record<T, string>; onChange: (v: T) => void }) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as T)}>
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(options) as T[]).map((k) => (
          <SelectItem key={k} value={k}>
            {options[k]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function StrategyTab() {
  const { entityType, refresh } = useOutletContext<EntityTypeOutlet>();
  const [f, setF] = useState<StrategyForm>(() => fromDoc(entityType.strategy ?? {}));
  const [msg, setMsg] = useState<string>();
  const set = (k: keyof StrategyForm, v: string) =>
    setF({ ...f, [k]: typeof f[k] === 'number' ? Number(v) : v });
  const invalid = !(0 <= f.review_threshold && f.review_threshold <= f.link_threshold && f.link_threshold <= 1)
    || !(0 <= f.no_link_veto_below && f.no_link_veto_below < f.review_threshold);

  async function save() {
    try {
      await api(`/api/entity-types/${entityType.id}/strategy`, { method: 'PUT', json: toDoc(f) });
      await refresh();
      setMsg('Saved. Publish from the Definition tab to freeze it as a new strategy version.');
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  const field = (k: keyof StrategyForm, label: string, hint: string, step?: string) => (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Input type={typeof f[k] === 'number' ? 'number' : 'text'} step={step} value={String(f[k])} onChange={(e) => set(k, e.target.value)} />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );

  const nearestBy = f.blocker === 'nearest_by_blocker';
  const llm = f.matcher === 'llm_entity_matcher';

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Resolution strategy</CardTitle>
          <CardDescription>
            A versioned composition of components: blocker → matcher → decision policy → clusterer → survivorship.
            Each run freezes the published version in its manifest.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 md:grid-cols-2">
          <div className="space-y-3">
            <h4 className="font-medium">Blocker ({f.blocker} v1)</h4>
            <ComponentSelect value={f.blocker} options={BLOCKERS} onChange={(v) => setF({ ...f, blocker: v })} />
            {field('embedding_endpoint', 'Embedding endpoint', 'Embeds the match representation (match text).')}
            {field('num_results', 'Candidates per record', 'Nearest neighbours retrieved per record.')}
            {field(
              'score_threshold',
              'Minimum similarity',
              nearestBy
                ? 'Cosine similarity; candidates below it are dropped.'
                : 'AI Search score, 1 / (1 + L2²); candidates below it are dropped. Equals cosine similarity only at 0.5.',
              '0.05',
            )}
            {nearestBy && (
              <div className="space-y-1">
                <Label>Search</Label>
                <ComponentSelect
                  value={f.search === 'approx' ? 'approx' : 'exact'}
                  options={{ exact: 'EXACT: score every record', approx: 'APPROX: let the optimizer approximate' }}
                  onChange={(v) => setF({ ...f, search: v })}
                />
              </div>
            )}
          </div>
          <div className="space-y-3">
            <h4 className="font-medium">Matcher ({f.matcher} v1)</h4>
            <ComponentSelect
              value={f.matcher}
              options={MATCHERS}
              onChange={(v) => setF({ ...f, matcher: v, concurrency: DEFAULT_CONCURRENCY[v] })}
            />
            {llm && field('llm_endpoint', 'LLM endpoint', `Uses the ${entityType.entity_kind} domain rules.`)}
            {!llm && (
              <p className="text-xs text-muted-foreground">
                Uses the {entityType.entity_kind} domain rules. Returns P(same entity) only, so the review queue shows no matcher rationale.
              </p>
            )}
            {field(
              'concurrency',
              'Concurrency',
              llm ? 'Parallel LLM calls in the engine job.' : 'Parallel ai_decide calls in the engine job (the endpoint rate-limits at about 100 calls/s).',
            )}
          </div>
          <div className="space-y-3">
            <h4 className="font-medium">Decision policy · thresholds (threshold_policy v1)</h4>
            {field('link_threshold', 'Auto-link at or above', 'P(match) at or above this links automatically.', '0.01')}
            {field('review_threshold', 'Review at or above', 'Between the two thresholds goes to the review queue.', '0.01')}
            {invalid && <p className="text-sm text-destructive">Need 0 ≤ review ≤ link ≤ 1, and the clustering guard below the review threshold.</p>}
          </div>
          <div className="space-y-2 text-sm text-muted-foreground">
            <h4 className="font-medium text-foreground">Clusterer & survivorship</h4>
            <p>constrained_components: human CANNOT_LINK beats MUST_LINK beats automatic links; entity ids are stable.</p>
            {field('no_link_veto_below', 'Keep apart confident non-matches below',
              'If the matcher scored two records below this, a chain of automatic links cannot put them in one entity (a human must-link still can). 0 turns it off.', '0.05')}
            <p>attribute_rules v1: per-attribute rules from the definition, with steward overrides and pinned golden records.</p>
          </div>
        </CardContent>
      </Card>
      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={invalid}>Save strategy draft</Button>
        {msg && <span className="text-sm text-muted-foreground">{msg}</span>}
      </div>
    </div>
  );
}
