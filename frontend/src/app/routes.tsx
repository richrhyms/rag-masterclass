import type { RouteObject } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { Layout } from './Layout'
import { LoginPage } from '@/pages/LoginPage'
import { chatRoutes } from '@/features/chat/routes'
import { ingestionRoutes } from '@/features/ingestion/routes'

export const appRoutes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <Layout />,
        children: [
          ...chatRoutes,
          ...ingestionRoutes,
        ],
      },
    ],
  },
]
