import { beforeAll, expect, it, vi } from 'vitest'
import { citationBoxStyle } from '../api/knowledgeChat'
import fixtures from './fixtures/knowledge-pdf-coordinates.json'

let pdfjs: typeof import('pdfjs-dist/legacy/build/pdf.mjs')
beforeAll(async () => {
  // No browser or canvas rendering is used; only the installed PDF.js viewport calculation.
  vi.stubGlobal('DOMMatrix', class DOMMatrix {})
  vi.stubGlobal('ImageData', class ImageData {})
  vi.stubGlobal('Path2D', class Path2D {})
  pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs')
// Windows cold PDF.js module loading can exceed the default setup budget.
}, 60_000)

function originalPdf(fixture: typeof fixtures[number]) {
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>', `<< /Type /Page /Parent 2 0 R /MediaBox [${fixture.media_box.join(' ')}] /CropBox [${fixture.cropbox.join(' ')}] /Rotate ${fixture.rotation} /UserUnit ${fixture.user_unit} /Resources << >> /Contents 4 0 R >>`, '<< /Length 0 >>\nstream\n\nendstream']
  let content = '%PDF-1.7\n'
  const offsets = [0]
  for (const [index, object] of objects.entries()) { offsets.push(content.length); content += `${index + 1} 0 obj\n${object}\nendobj\n` }
  const xref = content.length
  content += `xref\n0 5\n0000000000 65535 f \n${offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return new TextEncoder().encode(content)
}

it.each(fixtures)('maps parsed geometry through real PDF.js viewport: $name', async fixture => {
  const task = pdfjs.getDocument({ data: originalPdf(fixture) })
  try {
    const document = await task.promise, page = await document.getPage(1)
    const viewport = page.getViewport({ scale: fixture.scale })
    const actual = citationBoxStyle(fixture.bbox, fixture.page_height, viewport, fixture.to_pdf_transform)
    for (const key of ['left', 'top', 'width', 'height'] as const) expect(actual[key]).toBeCloseTo(fixture.expected[key], 8)
  } finally { await task.destroy() }
})
