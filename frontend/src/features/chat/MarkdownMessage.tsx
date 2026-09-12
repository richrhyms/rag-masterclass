import ReactMarkdown from 'react-markdown'

// Renders assistant message content as real Markdown instead of raw text.
// Element -> Tailwind class mapping is done manually via `components` rather
// than pulling in @tailwindcss/typography's `prose` plugin -- this is the
// only place in the app that needs markdown styling, so a handful of
// targeted classes is simpler than registering a whole plugin for it.
export function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown
        components={{
          p: ({ ...props }) => <p className="mb-2 last:mb-0" {...props} />,
          strong: ({ ...props }) => <strong className="font-semibold" {...props} />,
          em: ({ ...props }) => <em className="italic" {...props} />,
          h1: ({ ...props }) => <h1 className="text-base font-semibold mt-3 mb-1 first:mt-0" {...props} />,
          h2: ({ ...props }) => <h2 className="text-base font-semibold mt-3 mb-1 first:mt-0" {...props} />,
          h3: ({ ...props }) => <h3 className="text-sm font-semibold mt-3 mb-1 first:mt-0" {...props} />,
          ul: ({ ...props }) => <ul className="list-disc pl-5 mb-2 space-y-1" {...props} />,
          ol: ({ ...props }) => <ol className="list-decimal pl-5 mb-2 space-y-1" {...props} />,
          li: ({ ...props }) => <li className="leading-relaxed" {...props} />,
          hr: ({ ...props }) => <hr className="my-3 border-zinc-200" {...props} />,
          a: ({ ...props }) => <a className="text-indigo-600 underline" target="_blank" rel="noreferrer" {...props} />,
          code: ({ ...props }) => (
            <code className="bg-zinc-100 px-1 py-0.5 rounded text-xs font-mono" {...props} />
          ),
          pre: ({ ...props }) => (
            <pre className="bg-zinc-100 p-2 rounded text-xs font-mono overflow-x-auto mb-2" {...props} />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
