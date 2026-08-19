import type { RouteObject } from 'react-router-dom'
import { IngestionPage } from './IngestionPage'

export const ingestionRoutes: RouteObject[] = [
  {
    path: '/ingestion',
    element: <IngestionPage />,
  },
]
