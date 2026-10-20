/** Read-only descriptions of backend/app/presentation.py projections. */
export const projectionDefinitions = {
  cohesion: 'Mean relationship affinity mapped from −1…1 to 0…1; defaults to 50% when no ties exist.',
  trust: 'Mean directed relationship trust; the projection falls back to citizens’ trust baselines when that mean is zero or there are no ties.',
  evidence: 'One minus belief_accuracy, where belief_accuracy is evidence_for divided by total recorded belief evidence; 100% when no evidence exists.',
  influence: 'Bounded activity score from non-ignore actions, following count, and summed relationship trust; not audience reach.',
  stance: 'Mean belief stance mapped from −1…1 to 0…1; not an emotional sentiment measurement.',
  velocity: 'Topic content count divided by max(1, tick × 8), capped at 100%; not a measured propagation speed.',
  budget: 'Display indicator: max(0, 1 − recorded interventions / 20). The console does not enforce this as a spending limit.',
} as const

export const consoleActions = {
  context_card: { label: 'Publish counterevidence', description: 'Publishes a context card on “fictional civic event” from the first citizen. The target is not used.' },
  bridge_prompt: { label: 'Publish seeded post', description: 'Publishes a post on “fictional civic event” from the first citizen. The target is not used.' },
  slow_signal: { label: 'Increase fact-check probability', description: 'Raises the world’s fact-check probability by strength × 0.35, capped at 1. The target and rationale are not stored for this action.' },
  highlight_care: { label: 'Increase trust toward a citizen', description: 'Shifts every other citizen’s trust toward the selected citizen by max(0.02, intensity × 0.25). The rationale is not stored for this action.' },
} as const

export function consoleInputValid(input: { action: string; target: string; intensity: number; rationale: string }, nodeIds: string[]) {
  return input.action in consoleActions && nodeIds.includes(input.target) && Number.isFinite(input.intensity) && input.intensity >= 0 && input.intensity <= 1 && input.rationale.trim().length >= 5 && input.rationale.trim().length <= 280
}
