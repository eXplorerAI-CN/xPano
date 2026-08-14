import type { ReconstructionStatus } from '../../lib/contracts'

export function shouldAutoOpenResults(
  startedHere: boolean,
  running: boolean,
  status: ReconstructionStatus,
  inputRevision: number,
  alignmentInputRevision: number,
) {
  return startedHere
    && !running
    && status === 'complete'
    && inputRevision === alignmentInputRevision
}
