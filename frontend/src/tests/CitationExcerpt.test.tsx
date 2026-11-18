import { render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { MarkdownContent } from '../components/MarkdownContent'
import { CitationExcerpt } from '../components/CitationExcerpt'
import sourceStyles from '../components/KnowledgeSourcePanel.css?raw'
import markdownStyles from '../components/MarkdownContent.css?raw'
import workspaceStyles from '../pages/KnowledgeWorkspace.css?raw'

afterEach(() => document.head.querySelectorAll('[data-reading-test]').forEach(node => node.remove()))

function readingStyles() {
  const style = document.createElement('style')
  style.dataset.readingTest = ''
  // The page may be loaded after the source tools; its generic paragraph rule
  // must not restore arbitrary word breaks in either reading pane.
  style.textContent = markdownStyles + sourceStyles + workspaceStyles
  document.head.append(style)
}

it('lets PDF hard newlines flow within a paragraph and keeps whole English words', () => {
  readingStyles()
  const view = render(<div className="kw-source-body"><section className="kw-source-tools"><CitationExcerpt>{'Long lasting\nobservations remain useful.'}</CitationExcerpt></section></div>)
  const style = getComputedStyle(view.container.querySelector('blockquote')!)
  expect(style.whiteSpace).toBe('normal')
  expect(style.overflowWrap).toBe('normal')
  expect(style.wordBreak).toBe('normal')
  expect(style.hyphens).toBe('none')
  expect(style.overflowX).toBe('auto')
  expect(style.maxWidth).toBe('100%')
  expect(getComputedStyle(view.container.querySelector('blockquote p')!).overflowWrap).toBe('normal')
})

it.each([
  ['  Long lasting\nobservations.\n\nNext paragraph.  ', ['  Long lasting\nobservations.', 'Next paragraph.  ']],
  ['A pro-\nPPP-AR method.\r\n \t\r\n非差 α $x_i$.', ['A pro-\nPPP-AR method.', '非差 α $x_i$.']],
  ['', []],
])('retains literal evidence and blank-line paragraphs for %j', (text, paragraphs) => {
  const view = render(<CitationExcerpt>{text as string}</CitationExcerpt>)
  expect(view.container.querySelector('blockquote')!.textContent).toBe(text)
  expect(Array.from(view.container.querySelectorAll('p'), node => node.textContent)).toEqual(paragraphs)
})

it('keeps translated prose whole while retaining code, tables and equation formatting', () => {
  readingStyles()
  const text = 'Long lasting\nobservations remain useful.\n\n```text\none  two\nthree\n```\n\n| Name | Value |\n| --- | --- |\n| sample | 1 |\n\n$$\nx_i^2\n$$'
  const view = render(<div className="kw-source-body"><section className="kw-source-tools"><div className="kw-source-tools-translation"><MarkdownContent>{text}</MarkdownContent></div></section></div>)
  const paragraph = view.container.querySelector('.markdown-content p')!
  expect(getComputedStyle(paragraph).overflowWrap).toBe('normal')
  expect(getComputedStyle(paragraph).wordBreak).toBe('normal')
  expect(getComputedStyle(paragraph).hyphens).toBe('none')
  expect(getComputedStyle(view.container.querySelector('.markdown-content')!).overflowX).toBe('auto')
  const code = view.container.querySelector('pre > code')!
  expect(code.textContent).toBe('one  two\nthree\n')
  expect(getComputedStyle(code).whiteSpace).toBe('pre')
  expect(getComputedStyle(view.container.querySelector('pre')!).overflowX).toBe('auto')
  expect(getComputedStyle(view.container.querySelector('table')!).overflowX).toBe('auto')
  expect(getComputedStyle(view.container.querySelector('.katex-display > .katex')!).whiteSpace).toBe('nowrap')
})
