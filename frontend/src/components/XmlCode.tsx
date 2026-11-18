import type { ReactNode } from 'react'
import './XmlCode.css'

export function XmlCode({ content }: { content: string }) {
  const expression = /(<!--[\s\S]*?-->)|("[^"]*"|'[^']*')|([\w:-]+)(?=\s*=)|(<\/?[\w:-]+)|([<>?/]+)/g
  const nodes: ReactNode[] = []
  let position = 0
  for (const match of content.matchAll(expression)) {
    nodes.push(content.slice(position, match.index))
    const kind = match[1] ? 'comment' : match[2] ? 'value' : match[3] ? 'attribute' : match[4] ? 'tag' : 'punctuation'
    nodes.push(<span key={match.index} className={`xml-token-${kind}`}>{match[0]}</span>)
    position = match.index + match[0].length
  }
  nodes.push(content.slice(position))
  return <div className="xml-code-surface" role="region" aria-label="XML 全文" tabIndex={0}><pre>{nodes}</pre></div>
}
