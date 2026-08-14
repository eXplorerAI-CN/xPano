export function commandErrorMessage(error: unknown) {
  if (error && typeof error === 'object') {
    if ('message' in error && error.message) return String(error.message)
    if ('error' in error && error.error) return String(error.error)
    if ('details' in error && error.details) return String(error.details)
  }
  if (typeof error === 'string' && error.trim()) return error
  if (error !== null && error !== undefined) return String(error)
  return '未知错误'
}
