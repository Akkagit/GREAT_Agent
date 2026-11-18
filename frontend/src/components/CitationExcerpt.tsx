import { Fragment } from 'react'

/** PDF line endings are whitespace inside a paragraph. Blank lines still
 * separate paragraphs; keep their literal text nodes so textContent, selection
 * and the authoritative evidence string are never rewritten for display. */
export function CitationExcerpt({ children }: { children: string }) {
  const parts = children.split(/(\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*)/)
  return <blockquote className="kw-excerpt" aria-label="引用原文">
    {parts.map((part, index) => <Fragment key={index}>{index % 2 || !part ? part : <p>{part}</p>}</Fragment>)}
  </blockquote>
}
