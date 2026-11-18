import { screen } from '@testing-library/react'
import type { UserEvent } from '@testing-library/user-event'

export async function selectOption(user: UserEvent, name: string, option: string) {
  await user.click(await screen.findByRole('combobox', { name }))
  await user.click(await screen.findByRole('option', { name: option }))
}

export async function selectedOption(name: string) {
  const control = await screen.findByRole('combobox', { name })
  const label = control.closest('label')
  if (!label) throw new Error(`Missing label for ${name}`)
  return label
}
