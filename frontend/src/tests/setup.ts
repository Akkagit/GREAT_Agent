import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }),
})

globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// jsdom has no pseudo-element layout. Antd's scrollbar measurement requests a
// pseudo style, so return the real element style for that unsupported argument.
const computedStyle = window.getComputedStyle.bind(window)
window.getComputedStyle = element => computedStyle(element)

afterEach(cleanup)
