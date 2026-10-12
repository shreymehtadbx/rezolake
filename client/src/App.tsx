import { createBrowserRouter, Link, Outlet, RouterProvider, useRouteError } from 'react-router';
import { Badge } from '@databricks/appkit-ui/react';
import { ConfigContext, type AppConfig } from './lib/context';
import { useApi } from './lib/hooks';
import { ErrorNote, Loading } from './components/states';
import { EntityTypesPage } from './pages/EntityTypesPage';
import { EntityTypeLayout } from './pages/EntityTypeLayout';
import { OverviewTab } from './pages/OverviewTab';
import { DefinitionTab } from './pages/DefinitionTab';
import { StrategyTab } from './pages/StrategyTab';
import { RunsTab } from './pages/RunsTab';
import { ReviewTab } from './pages/ReviewTab';
import { EntitiesTab } from './pages/EntitiesTab';
import { EntityDetailPage } from './pages/EntityDetailPage';
import { ActivityTab } from './pages/ActivityTab';

function Layout() {
  const { data, error } = useApi<AppConfig>('/api/config');
  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b px-4 md:px-6 py-3 flex items-center gap-4">
        <Link to="/" className="text-lg font-semibold text-foreground">Rezolake</Link>
        <span className="text-sm text-muted-foreground hidden sm:inline">Master data management on Databricks</span>
        {data && (
          <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">{data.catalog}</Badge>
            <span title="Signed-in user">{data.user}</span>
          </div>
        )}
      </header>
      <main className="flex-1 p-4 md:p-6">
        {error && <ErrorNote error={error} title="Could not reach the app API" />}
        {!data && !error && <Loading rows={4} />}
        {data && (
          <ConfigContext.Provider value={data}>
            <Outlet />
          </ConfigContext.Provider>
        )}
      </main>
    </div>
  );
}

function RouteError() {
  const err = useRouteError();
  return (
    <div className="p-6 max-w-3xl mx-auto space-y-3">
      <ErrorNote error={err instanceof Error ? err : String(err)} title="This page hit an error" />
      <Link className="underline" to="/">Back to entity types</Link>
    </div>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      { path: '/', element: <EntityTypesPage /> },
      {
        path: '/t/:id',
        element: <EntityTypeLayout />,
        children: [
          { index: true, element: <OverviewTab /> },
          { path: 'definition', element: <DefinitionTab /> },
          { path: 'strategy', element: <StrategyTab /> },
          { path: 'runs', element: <RunsTab /> },
          { path: 'review', element: <ReviewTab /> },
          { path: 'entities', element: <EntitiesTab /> },
          { path: 'entities/:entityId', element: <EntityDetailPage /> },
          { path: 'activity', element: <ActivityTab /> },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
