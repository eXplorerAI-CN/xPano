export function framesPerSecondForLimit(duration: number, frameLimit: number, fallback: number) {
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(frameLimit) || frameLimit <= 0) return fallback
  return Number((frameLimit / duration).toFixed(3))
}

export function frameTimestamp(start: number, oneBasedIndex: number, framesPerSecond: number) {
  if (!Number.isFinite(framesPerSecond) || framesPerSecond <= 0) throw new Error('framesPerSecond must be greater than 0')
  return start + (oneBasedIndex - 1) / framesPerSecond
}
