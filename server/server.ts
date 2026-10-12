import { createApp, analytics, jobs, lakebase, server } from '@databricks/appkit';
import { engineGrants, SCHEMA_DDL } from './lib/schema';
import { registerApi, type Deps } from './routes/api';
import { registerOrchestration } from './routes/orchestration';

createApp({
  // Mastered data changes whenever an engine run finishes; AppKit's default analytics cache
  // (1h TTL, persisted in Lakebase) would keep serving pre-run results. RT queries are ~1s, so read live.
  cache: { enabled: false },
  plugins: [analytics(), lakebase(), jobs(), server()],
  async onPluginsReady(appkit) {
    for (const stmt of SCHEMA_DDL) {
      try {
        await appkit.lakebase.query(stmt);
      } catch (err) {
        // Local dev runs as a user who can use, but not own, the SP-owned objects.
        if (process.env.NODE_ENV === 'production') throw err;
        console.warn('[rezolake] schema step skipped:', (err as Error).message);
      }
    }
    console.log('[rezolake] Lakebase schema ready');

    const job = appkit.jobs('default');
    // The engine job runs as its own identity: grant it DML on the app-owned schema.
    try {
      const def = await job.getJob();
      // The engine job's parameter defaults are the single source of environment config (set per bundle target).
      const catalogParam = def.ok ? def.data.settings?.parameters?.find((p) => p.name === 'catalog')?.default : undefined;
      if (catalogParam && !process.env.REZOLAKE_CATALOG) process.env.REZOLAKE_CATALOG = catalogParam;
      console.log(`[rezolake] catalog = ${process.env.REZOLAKE_CATALOG ?? '(unset)'}`);
      const role = process.env.REZOLAKE_ENGINE_ROLE ?? (def.ok ? def.data.run_as_user_name ?? def.data.creator_user_name : undefined);
      if (role) {
        for (const stmt of engineGrants(role)) await appkit.lakebase.query(stmt);
        console.log(`[rezolake] granted engine role ${role}`);
      }
    } catch (err) {
      console.warn('[rezolake] engine grant failed:', (err as Error).message);
    }

    const deps: Deps = {
        query: (text, params) => appkit.lakebase.query(text, params),
        async runJob(params) {
          const r = await job.runNow({
            job_parameters: { ...params, app_sp: process.env.DATABRICKS_CLIENT_ID ?? '' },
          });
          if (!r.ok || !r.data.run_id) throw new Error(`failed to start engine job (${r.ok ? 'no run id' : r.status})`);
          return r.data.run_id;
        },
        async getJobRun(runId) {
          const r = await job.getRun(runId);
          if (!r.ok) return null;
          return {
            lifeCycle: r.data.state?.life_cycle_state,
            result: r.data.state?.result_state,
            message: r.data.state?.state_message,
          };
        },
        async cancelJobRun(runId) {
          await job.cancelRun(runId);
        },
    };
    appkit.server.extend((app) => {
      registerApi(app, deps);
      registerOrchestration(app, deps);
    });
  },
}).catch(console.error);
