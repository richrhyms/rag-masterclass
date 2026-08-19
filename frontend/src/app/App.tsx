import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import { AuthProvider } from './AuthProvider'
import { appRoutes } from './routes'

const router = createBrowserRouter(appRoutes)

function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  )
}

export default App
