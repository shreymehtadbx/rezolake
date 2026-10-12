import type { sql } from '@databricks/appkit-ui/js';

/** Query parameters shared by the per-entity queries. */
export type EntityParams = {
  catalog: ReturnType<typeof sql.string>;
  entity_type_id: ReturnType<typeof sql.string>;
  entity_id: ReturnType<typeof sql.string>;
};

/** Queue a steward command; the engine applies it on its next run. */
export type OnCommand = (type: string, payload: Record<string, unknown>, label: string) => Promise<void>;
