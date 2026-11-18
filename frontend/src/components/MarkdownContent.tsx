import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkCjkFriendly from 'remark-cjk-friendly/parseOnly'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import './MarkdownContent.css'

interface MarkdownContentProps {
  children: string
  className?: string
}

/** Accept common TeX delimiters only in prose; fenced, indented and inline code
 * is copied byte-for-byte before Markdown gets to interpret it. */
function normalizeMathDelimiters(source: string) {
  const prose = (text: string) => text.replace(/^([ \t]*)\$\$(.+?)\$\$[ \t]*$/gm, (_, indent: string, math: string) => `${indent}$$\n${math}\n$$`)
      .replace(/(?<!\\)\\\(([\s\S]*?)\\\)/g, (_, math: string) => `$${math}$`)
      .replace(/(?<!\\)\\\[([\s\S]*?)\\\]/g, (_, math: string) => `\n\n$$\n${math.trim()}\n$$\n\n`)
  // A code span closes only at an entire backtick run of the same length.
  // Neither delimiter may be a prefix or suffix of a longer run.
  const code = /(^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)[\s\S]*?(?:^ {0,3}\2[ \t]*(?:\n|$)|(?![\s\S]))|^(?: {4}|\t)[^\n]*(?:\n|$)|(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\3(?!`))/gm
  let result = '', cursor = 0
  for (const match of source.matchAll(code)) {
    result += prose(source.slice(cursor, match.index)) + match[0]
    cursor = match.index + match[0].length
  }
  return result + prose(source.slice(cursor))
}

/** Shared renderer for assistant prose. Raw HTML stays disabled and URLs use
 * react-markdown's safe default protocol filter. Code remains literal text. */
export function MarkdownContent({ children, className }: MarkdownContentProps) {
  return <div className={['markdown-content', className].filter(Boolean).join(' ')}>
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkMath]} rehypePlugins={[[rehypeKatex, { trust: false, throwOnError: false, strict: 'ignore' }]]} skipHtml>{normalizeMathDelimiters(children)}</ReactMarkdown>
  </div>
}
