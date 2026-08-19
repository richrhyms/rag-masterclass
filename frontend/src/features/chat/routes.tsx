import type { RouteObject } from 'react-router-dom'
import { ChatPage } from './ChatPage'

export const chatRoutes: RouteObject[] = [
  {
    path: '/',
    element: <ChatPage />,
  },
  {
    path: '/chat/:threadId',
    element: <ChatPage />,
  },
]
