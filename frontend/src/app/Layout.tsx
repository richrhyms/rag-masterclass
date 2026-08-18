import { Outlet, Link, useNavigate } from 'react-router-dom'
import { useAuth } from './AuthProvider'
import { MessageSquare, Database, LogOut, Menu, X } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

export function Layout() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [isSidebarOpen, setIsSidebarOpen] = useState(true)

  const handleSignOut = async () => {
    await signOut()
    navigate('/login')
  }

  return (
    <div className="flex h-screen bg-zinc-50 text-zinc-900">
      {/* Sidebar */}
      <aside className={cn(
        "bg-zinc-900 text-zinc-100 transition-all duration-300 flex flex-col",
        isSidebarOpen ? "w-64" : "w-0 overflow-hidden"
      )}>
        <div className="p-4 font-bold text-xl flex items-center gap-2 border-b border-zinc-800">
          <Database className="w-6 h-6 text-indigo-400" />
          <span>RAG Masterclass</span>
        </div>

        <nav className="flex-1 p-4 space-y-2">
          <Link
            to="/"
            className="flex items-center gap-3 p-2 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            <MessageSquare className="w-5 h-5" />
            <span>Chat</span>
          </Link>
          <Link
            to="/ingestion"
            className="flex items-center gap-3 p-2 rounded-lg hover:bg-zinc-800 transition-colors"
          >
            <Database className="w-5 h-5" />
            <span>Ingestion</span>
          </Link>
        </nav>

        <div className="p-4 border-t border-zinc-800">
          <div className="flex items-center gap-3 mb-4 px-2">
            <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-sm font-medium">
              {user?.email?.[0].toUpperCase() || 'U'}
            </div>
            <div className="truncate text-sm opacity-80">
              {user?.email}
            </div>
          </div>
          <button
            onClick={handleSignOut}
            className="flex items-center gap-3 w-full p-2 rounded-lg hover:bg-red-900/30 hover:text-red-400 transition-colors text-sm"
          >
            <LogOut className="w-5 h-5" />
            <span>Sign Out</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden">
        <header className="h-16 bg-white border-b flex items-center px-4 gap-4">
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-2 hover:bg-zinc-100 rounded-md"
          >
            {isSidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
          <h1 className="font-semibold">Application</h1>
        </header>
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
